"""STT: распознавание речи (faster-whisper, CPU) — многоканальный v14.

Микрофон и (опционально) LOOPBACK-канал — весь звук системы (созвон/стрим):
Нимфея слышит не только владельца, но и собеседника. Каждый канал — свой
экземпляр STTModule: on_utterance(text, channel), транскрипты помечены
источником ('mic' / 'loop').

Фразы нарезаются по энергии (RMS) с гистерезисом: тишина STT_SILENCE_CUT
секунд = конец фразы. Защита от самоуслышивания: pause() глушит канал,
пока Нимфея говорит (пайплайн глушит ОБА канала — её голос играет в динамики
и попадал бы в loopback).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

import core.pipeline_trace as trace
from core.config import (STT_BEAM, STT_BEAM_FINAL, STT_BARGE_RMS_MULT,
                         STT_COMPRESSION_THRESHOLD, STT_COMPUTE, STT_CPU_THREADS,
                         STT_DEVICE, STT_VRAM_MIN_MIB,
                         STT_HALLUCINATION_PHRASES, STT_LANGUAGE,
                         STT_LOGPROB_THRESHOLD, STT_LOOP_MIN_TEXT_CHARS,
                         STT_LOOP_MIN_WORDS, STT_LOOP_PROMPT, STT_MAX_UTTERANCE,
                         STT_MIC_GAIN, STT_MIC_PROMPT, STT_MIN_SPEECH,
                         STT_MIN_TEXT_CHARS, STT_MODEL, STT_NO_SPEECH_THRESHOLD,
                         STT_PARTIAL_SEC, STT_REPEAT_FILTER,
                         STT_REPEAT_MAX_UNIQUE_RATIO, STT_REPEAT_MIN_WORDS,
                         STT_RMS_THRESHOLD, STT_RMS_NOISE_MULT,
                         STT_SAMPLE_RATE, STT_SILENCE_CUT,
                         STT_VAD_MIN_SILENCE_MS, STT_WHISPER_VAD)

log = logging.getLogger("stt")

_BLOCK = 0.1  # сек на блок микрофона

# --- устройство: авто CUDA при достатке VRAM, иначе CPU -------------------
# Решение принимается ОДИН раз на канал (модульный кэш): mic грузится первым
# и забирает GPU, loop проверяет остаток — не влезает, живёт на CPU.
_STT_DEVICE_RESOLVED: dict[str, tuple[str, str]] = {}


def _add_nvidia_dll_dirs() -> None:
    """cuBLAS/cuDNN из pip-пакетов (nvidia-cublas-cu12, nvidia-cudnn-cu12):
    без add_dll_directory ctranslate2 не находит cublas64_12.dll (WinError 126)."""
    import site
    for base in list(site.getsitepackages()) + [site.getusersitepackages()]:
        for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin"):
            p = os.path.join(base, sub.replace("/", os.sep))
            if os.path.isdir(p):
                try:
                    os.add_dll_directory(p)
                    os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass


def _free_vram_mib() -> int:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            timeout=5, text=True)
        return int(out.strip().splitlines()[0])
    except Exception:  # noqa: BLE001 — нет nvidia-smi/драйвера → GPU нет
        return 0


def _resolve_stt_device(channel: str) -> tuple[str, str]:
    """(device, compute_type) для этого канала: явный env, иначе CUDA при
    свободных ≥ STT_VRAM_MIN_MIB МиБ, иначе CPU. CUDA быстрее втрое
    (small fp16 1.4 с vs CPU int8 3.5-4.5 с на 6 с речи, замер 13.09)."""
    if channel in _STT_DEVICE_RESOLVED:
        return _STT_DEVICE_RESOLVED[channel]
    if STT_DEVICE:  # явный выбор из env
        dev = STT_DEVICE
    else:
        dev = "cuda" if _free_vram_mib() >= STT_VRAM_MIN_MIB else "cpu"
    result = (dev, "float16" if dev == "cuda" else STT_COMPUTE)
    _STT_DEVICE_RESOLVED[channel] = result
    return result


class STTModule:
    def __init__(self, on_utterance, model_size: str = STT_MODEL, *,
                 channel: str = "mic", loopback: bool = False,
                 rms_threshold: float | None = None, device_name: str = "",
                 shared_model=None, on_partial=None) -> None:
        self.on_utterance = on_utterance          # callable(text: str, channel: str, audio)
        self.on_partial = on_partial              # callable(text) — недоговорённая фраза (barge-in)
        self.model_size = model_size
        self.channel = channel
        self.loopback = loopback                  # True = WASAPI loopback (звук системы)
        self.rms_threshold = STT_RMS_THRESHOLD if rms_threshold is None else rms_threshold
        self.device_name = device_name            # имя/номер устройства вывода для loopback
        self._model = shared_model                # общая модель whisper (экономия RAM)
        self._owns_model = shared_model is None
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._recognizer: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._barge = threading.Event()           # режим перебивания: слушаем, пока Нима говорит
        self._defer = threading.Event()           # отложенный слух: копим буфер, распознаём после её речи
        self._speaking = False                    # служебное: сейчас идёт сегмент речи
        self.state = "idle"
        # Тихий микрофон: программное усиление ДО VAD (только канал mic;
        # loopback-звук системный, там усиление не нужно). Живой замер v14.8.2:
        # DEXP U700 даёт речь ~0.0026 RMS при пороге 0.004 — почти глухота.
        self._mic_gain = 1.0 if self.loopback else STT_MIC_GAIN

    # --- жизнь модуля ---
    def start(self) -> None:
        """Грузит модель СИНХРОННО (в потоке вызывающего), потом стартует потоки.

        Последовательность важна: нативные DLL (ctranslate2/torch/cudnn) не
        должны грузиться параллельно из разных потоков — на этой машине это
        роняло процесс (урок v13).
        """
        if self._model is None:
            self._load_model()
        self._stop.clear()
        self._thread = threading.Thread(target=self._capture_loop, name=f"SttMic-{self.channel}",
                                        daemon=True)
        self._thread.start()
        self._recognizer = threading.Thread(target=self._recognizer_loop, name=f"SttRec-{self.channel}",
                                            daemon=True)
        self._recognizer.start()
        self.state = "listening"
        log.info("STT[%s] запущен (модель %s, %s/%s%s)",
                 self.channel, self.model_size, STT_DEVICE, STT_COMPUTE,
                 f", усиление x{self._mic_gain:g}" if self._mic_gain != 1.0 else "")

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(None)

    def restart(self) -> None:
        """Перезапуск потоков (watchdog v14.7): модель уже загружена, грузим
        заново только потоки захвата/распознавания. Живые потоки глушим."""
        self.stop()
        time.sleep(0.3)
        self._stop.clear()
        self._paused.clear()
        self._barge.clear()
        self._defer.clear()
        self._thread = threading.Thread(target=self._capture_loop,
                                        name=f"SttMic-{self.channel}", daemon=True)
        self._thread.start()
        self._recognizer = threading.Thread(target=self._recognizer_loop,
                                            name=f"SttRec-{self.channel}", daemon=True)
        self._recognizer.start()
        self.state = "listening"
        log.info("[WARNING] STT[%s] перезапущен watchdog'ом", self.channel)

    def pause(self, value: bool = True) -> None:
        if value:
            self._paused.set()
            self.state = "paused"
        else:
            self._paused.clear()
            self.state = "listening"

    def set_barge(self, value: bool = True) -> None:
        """Режим перебивания: микрофон НЕ глохнет, пока Нимфея говорит.

        Порог RMS поднимается (её эхо из динамиков слабее прямой речи),
        распознанные фразы уходят в обработчик перебивания."""
        if value:
            self._barge.set()
        else:
            self._barge.clear()

    def set_defer(self, value: bool = True) -> None:
        """Отложенный слух (матрица слышимости v14.5, пункт 4): пока Нимфея
        говорит, loopback НЕ глушится, а копит буфер; сразу после её реплики
        буфер распознаётся и уходит в пайплайн. Так собеседник в созвоне,
        заговоривший во время её речи, не теряется — а её собственный голос
        из буфера отфильтрует эхо-фильтр пайплайна."""
        if value:
            self._defer.set()
        else:
            self._defer.clear()

    # --- захват аудио ---
    def _capture_loop(self) -> None:
        if self.loopback:
            self._loopback_loop()   # WASAPI loopback — через PyAudioWPatch
            return
        import sounddevice as sd

        def callback(indata, _frames, _time_info, status):
            if status:
                pass  # overflow — просто пропускаем, следующий блок догонит
            self._queue.put(bytes(indata))

        # Конкретный микрофон по имени/номеру (device_name). Нужно, когда
        # устройство ВВОДА по умолчанию — виртуальное (напр. VoiceMeeter Out B1,
        # выбранное микрофоном для созвона): тогда Нима должна слушать физический
        # микрофон напрямую (напр. "DEXP U700"), а не микс из VoiceMeeter (иначе
        # слышит саму себя / тишину). Пусто = устройство по умолчанию.
        dev = self._resolve_input_device(sd)
        try:
            with sd.InputStream(samplerate=STT_SAMPLE_RATE, channels=1,
                                dtype="int16", blocksize=int(STT_SAMPLE_RATE * _BLOCK),
                                device=dev, callback=callback):
                log.info("STT[%s] микрофон: %s", self.channel,
                         sd.query_devices(dev)["name"] if dev is not None else "по умолчанию")
                while not self._stop.is_set():
                    time.sleep(0.1)
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] STT[%s] аудио-поток не открылся: %s", self.channel, exc)

    def _resolve_input_device(self, sd) -> int | None:
        """Индекс микрофона по имени/номеру из device_name (подстрока, регистр
        не важен). Пусто/не найдено → None (устройство ввода по умолчанию)."""
        name = (self.device_name or "").strip().lower()
        if not name:
            return None
        try:
            if name.isdigit():
                return int(name)
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) > 0 and name in dev["name"].lower():
                    return idx
        except Exception as exc:  # noqa: BLE001
            log.warning("[WARNING] STT[%s] микрофон '%s' не найден: %s — беру по умолчанию",
                        self.channel, self.device_name, exc)
        return None

    def _loopback_loop(self) -> None:
        """Весь звук системы (созвон/стрим). sounddevice не умеет WASAPI
        loopback — используется PyAudioWPatch; устройство работает на своей
        частоте (обычно 48 кГц), recognizer ресемплирует в 16 кГц.

        БЛОКИРУЮЩЕЕ чтение, не callback: C-механизм колбеков PyAudioWPatch на
        этой машине не доставляет аудио (SystemError getargs, живой тест
        v14.6), а stream.read() в потоке захвата работает надёжно.
        Живой тест v14.6 #2: когда рендер-устройство молчит, WASAPI-loopback
        стрим уходит в stopped и is_active() становится False — цикл чтения
        тихо умирал навсегда. Теперь стрим пересоздаётся при остановке/ошибке.
        """
        import pyaudiowpatch as pyaudio

        stream = None
        pa = None
        retry_delay = 5.0
        while not self._stop.is_set():
            if stream is None:
                try:
                    pa = pyaudio.PyAudio()
                    device = self._resolve_loopback_device(pa)
                    rate = int(device["defaultSampleRate"])
                    self._capture_rate = rate
                    block = int(rate * _BLOCK)
                    stream = pa.open(format=pyaudio.paInt16, channels=2, rate=rate,
                                     input=True, input_device_index=int(device["index"]),
                                     frames_per_buffer=block)
                    stream.start_stream()
                    retry_delay = 5.0
                    self._lb_open_fails = 0
                    self.state = "listening"
                    log.info("STT[%s] loopback: %s (%d Гц, блокирующее чтение)",
                             self.channel, device["name"], rate)
                except Exception as exc:  # noqa: BLE001
                    self.state = "degraded"
                    # Именованный loopback может стать неоткрываемым навсегда
                    # (живой тест v14.8.1: после смены версий VoiceMeeter endpoint
                    # «Динамики (HDA)» отвечает -9996 Invalid device бесконечно,
                    # хотя числится активным). После 3 неудач подряд уходим на
                    # loopback по умолчанию (дефолтный вывод системы) — слушать
                    # лучше что-то, чем не слушать ничего.
                    self._lb_open_fails = getattr(self, "_lb_open_fails", 0) + 1
                    if self.device_name and self._lb_open_fails >= 3:
                        log.warning("[WARNING] STT[%s] loopback %r не открывается "
                                    "(%d попыток) — перехожу на loopback по умолчанию",
                                    self.channel, self.device_name, self._lb_open_fails)
                        self.device_name = ""
                    log.error("[ERROR] STT[%s] loopback не открылся: %s — "
                              "повтор через %.0f с", self.channel, exc, retry_delay)
                    if pa:
                        try:
                            pa.terminate()
                        except Exception:  # noqa: BLE001
                            pass
                    pa = stream = None
                    self._stop.wait(retry_delay)
                    retry_delay = min(retry_delay * 2.0, 60.0)
                    continue
            try:
                data = stream.read(block, exception_on_overflow=False)
                if not stream.is_active():
                    # устройство молчало → WASAPI-стрим остановился; поднимаем
                    stream.start_stream()
                    log.info("STT[%s] loopback: стрим реанимирован", self.channel)
            except OSError as exc:
                log.warning("[WARNING] STT[%s] loopback read: %s — пересоздаю стрим",
                            self.channel, exc)
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
                if pa:
                    try:
                        pa.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                pa = stream = None
                continue
            if data:
                self._queue.put(data)
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        if pa:
            try:
                pa.terminate()
            except Exception:  # noqa: BLE001
                pass

    def _resolve_loopback_device(self, pa) -> dict:
        """Выбрать только реально открываемое WASAPI loopback-устройство.

        Индексы PyAudio меняются после переподключения/перезапуска VoiceMeeter.
        Поэтому числовой индекс принимается лишь если он есть в текущем списке,
        а имя ранжируется по точному совпадению до fallback на default.
        """
        name = (self.device_name or "").strip().lower()
        loopbacks = [info for info in pa.get_device_info_generator()
                     if info.get("isLoopbackDevice") and info.get("maxInputChannels", 0) > 0]
        if name.isdigit():
            index = int(name)
            for info in loopbacks:
                if int(info["index"]) == index:
                    return info
            raise RuntimeError(f"loopback index {index} отсутствует; доступны: "
                               f"{self._device_names(loopbacks)}")
        if name:
            exact = [info for info in loopbacks if info["name"].lower() == name]
            partial = [info for info in loopbacks if name in info["name"].lower()]
            matches = exact or partial
            if matches:
                return matches[0]
            log.warning("[WARNING] STT[%s] loopback '%s' не найден; пробую default. "
                        "Доступны: %s", self.channel, self.device_name,
                        self._device_names(loopbacks))
        try:
            default = pa.get_default_wasapi_loopback()
            if default.get("isLoopbackDevice") and default.get("maxInputChannels", 0) > 0:
                return default
        except OSError:
            pass
        if loopbacks:
            return loopbacks[0]
        raise RuntimeError("WASAPI loopback-устройства не найдены")

    @staticmethod
    def _device_names(devices: list[dict]) -> str:
        return ", ".join(f"{d.get('index')}:{d.get('name')}" for d in devices) or "нет"

    # --- сегментация + распознавание ---
    def _recognizer_loop(self) -> None:
        block_bytes = int(STT_SAMPLE_RATE * _BLOCK) * 4  # стерео int16 = 4 байта на блок-сэмпл
        buffer: list[np.ndarray] = []
        preroll: deque[np.ndarray] = deque(maxlen=6)  # 0.6 с до первого громкого блока:
                                                      # тихий зачин фразы («Ни-ма…»)
        defer_buffer: list[np.ndarray] = []   # сказанное, ПОКА она говорила
        defer_speech = 0.0
        silence_after_speech = 0.0
        speech_len = 0.0
        noise_floor = self.rms_threshold
        next_partial = 0.0

        while not self._stop.is_set():
            raw = self._queue.get()
            if raw is None:
                break
            block = np.frombuffer(raw, dtype=np.int16)
            if self.loopback:
                rate = getattr(self, "_capture_rate", STT_SAMPLE_RATE)
                block = block.reshape(-1, 2).mean(axis=1).astype(np.int16)
                if rate != STT_SAMPLE_RATE:  # ресемплинг устройства → 16 кГц
                    n_out = int(len(block) * STT_SAMPLE_RATE / rate)
                    block = np.interp(
                        np.linspace(0, len(block) - 1, n_out),
                        np.arange(len(block)), block.astype(np.float32)).astype(np.int16)
            block = block.astype(np.float32) / 32768.0
            if self._mic_gain != 1.0:
                block = block * self._mic_gain
            if self._paused.is_set():
                buffer.clear()
                defer_buffer.clear()
                defer_speech = silence_after_speech = speech_len = 0.0
                self._speaking = False
                continue

            rms = float(np.sqrt(np.mean(block * block)))
            noise_floor = 0.99 * noise_floor + 0.01 * rms  # медленная адаптация к фону
            # в режиме перебивания порог выше: её эхо из динамиков тише прямой речи
            threshold = max(self.rms_threshold, noise_floor * STT_RMS_NOISE_MULT)
            if self._barge.is_set():
                threshold *= STT_BARGE_RMS_MULT
            loud = rms > threshold

            # отложенный слух: копим то, что звучит во время её речи
            if self._defer.is_set():
                if loud:
                    defer_buffer.append(block)
                    defer_speech += _BLOCK
                elif defer_speech:
                    defer_buffer.append(block)   # хвост тишины внутри фразы
                # буфер ограничен ~30 с (её монолог может быть долгим)
                if len(defer_buffer) * _BLOCK > 30:
                    del defer_buffer[:len(defer_buffer) - int(30 / _BLOCK)]
                continue

            # defer сняли — режем буфер на ФРАЗЫ по тишине и отдаём каждую
            # отдельно: в буфере смешаны её эхо и, возможно, чужая речь;
            # эхо-фильтр пайплайна работает на отдельных фразах (её — выкинет,
            # чужие — пропустит), на всей куче он выкинул бы и чужое
            if defer_speech:
                segs: list[list[np.ndarray]] = []
                cur: list[np.ndarray] = []
                cur_speech = 0.0
                seg_silence = 0.0
                for blk in defer_buffer:
                    r = float(np.sqrt(np.mean(blk * blk)))
                    if r > self.rms_threshold:
                        cur.append(blk)
                        cur_speech += _BLOCK
                        seg_silence = 0.0
                    elif cur:
                        cur.append(blk)
                        seg_silence += _BLOCK
                        if seg_silence >= STT_SILENCE_CUT:
                            if cur_speech >= STT_MIN_SPEECH:
                                segs.append(cur)
                            cur, cur_speech, seg_silence = [], 0.0, 0.0
                if cur and cur_speech >= STT_MIN_SPEECH:
                    segs.append(cur)
                defer_buffer.clear()
                defer_speech = 0.0
                for seg in segs:
                    audio = np.concatenate(seg)
                    text = self._transcribe(audio)
                    if text:
                        try:
                            self.on_utterance(text, self.channel, audio)
                        except Exception:  # noqa: BLE001
                            log.exception("[ERROR] обработчик отложенной фразы упал")

            if loud:
                if not self._speaking:
                    buffer.extend(preroll)   # подмешиваем тихий зачин фразы
                    preroll.clear()
                buffer.append(block)
                speech_len += _BLOCK
                silence_after_speech = 0.0
                if not self._speaking:
                    self._speaking = True
                    self.state = "hearing" if not self._barge.is_set() else "barge"
                    next_partial = time.time() + STT_PARTIAL_SEC
            elif self._speaking:
                buffer.append(block)  # хвост тишины внутри фразы
                silence_after_speech += _BLOCK
            else:
                preroll.append(block)  # тишина вне фразы — буфер предпрослушивания

            # частичный скан недоговорённой фразы: жёсткие триггеры («стоп!»)
            # глушат речь НЕ дожидаясь конца фразы
            if (self._barge.is_set() and self._speaking and self.on_partial
                    and silence_after_speech < _BLOCK
                    and speech_len >= 0.6 and time.time() >= next_partial):
                next_partial = time.time() + STT_PARTIAL_SEC
                try:
                    partial = self._transcribe(np.concatenate(buffer), partial=True)
                    if partial:
                        self.on_partial(partial)
                except Exception:  # noqa: BLE001
                    log.exception("[ERROR] частичный скан упал")

            total = len(buffer) * _BLOCK
            cut = (self._speaking and silence_after_speech >= STT_SILENCE_CUT) \
                or total >= STT_MAX_UTTERANCE
            if cut:
                audio = np.concatenate(buffer) if buffer else None
                buffer.clear()
                was_speech = speech_len >= STT_MIN_SPEECH
                speech_len = silence_after_speech = 0.0
                self._speaking = False
                self.state = "listening"
                if audio is None or not was_speech:
                    continue
                rid = trace.new_request()
                trace.mark("speech_end", rid, info=f"{self.channel}; audio={len(audio) / STT_SAMPLE_RATE:.2f}s")
                trace.mark("stt_start", rid)
                text = self._transcribe(audio)
                trace.mark("stt_done", rid, text=text)
                if text:
                    try:
                        self._emit_utterance(text, audio, rid)
                    except Exception:  # noqa: BLE001
                        log.exception("[ERROR] обработчик фразы упал")

    def _emit_utterance(self, text: str, audio: np.ndarray, request_id: str) -> None:
        """Передать фразу, сохраняя совместимость со старыми callback из 3 аргументов."""
        try:
            self.on_utterance(text, self.channel, audio, request_id)
        except TypeError:
            self.on_utterance(text, self.channel, audio)

    def _load_model(self) -> None:
        self._preload_torch_cudnn()
        _add_nvidia_dll_dirs()
        from faster_whisper import WhisperModel
        device, compute = _resolve_stt_device(self.channel)
        t0 = time.time()
        self._model = WhisperModel(self.model_size, device=device,
                                   compute_type=compute,
                                   cpu_threads=STT_CPU_THREADS)
        log.info("faster-whisper[%s] загружена за %.1f с (%s, %s/%s)", self.channel,
                 time.time() - t0, self.model_size, device, compute)
        # прогрев на тишине, чтобы первая фраза не ждала инициализацию
        self._model.transcribe(np.zeros(STT_SAMPLE_RATE, dtype=np.float32),
                                language=STT_LANGUAGE, beam_size=1)

    @staticmethod
    def _preload_torch_cudnn() -> None:
        """Урок v13: ctranslate2 тащит СВОЮ cudnn64_9.dll — если она грузится в
        процесс раньше, torch не находит в ней свои символы и процесс падает
        (cudnnGetLibConfig, error 127). Предзагружаем cudnn из torch/lib:
        Windows не грузит вторую копию DLL с тем же именем модуля."""
        try:
            import ctypes
            import torch
            torch_lib = Path(torch.__file__).parent / "lib"
            os.add_dll_directory(str(torch_lib))
            for dll in ("cudnn_ops64_9.dll", "cudnn_cnn64_9.dll", "cudnn_graph64_9.dll",
                        "cudnn_heuristic64_9.dll", "cudnn_engines_precompiled64_9.dll",
                        "cudnn_engines_runtime_compiled64_9.dll", "cudnn_adv64_9.dll",
                        "cudnn64_9.dll"):
                try:
                    ctypes.WinDLL(str(torch_lib / dll))
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001 — torch может отсутствовать, не критично
            log.info("torch-cudnn предзагрузка пропущена: %s", exc)

    def _transcribe(self, audio: np.ndarray, partial: bool = False) -> str:
        """Распознавание сегмента. partial=True — быстрый скан недоговорённой
        фразы (нужен только для триггера «стоп»): beam=1, точность неважна.
        partial=False — финальная фраза: beam повыше (точнее падежи/имена) +
        пороги против галлюцинаций Whisper (temperature=0, no_speech/logprob/
        compression) и фильтр фраз-галлюцинаций («Спасибо за просмотр» и т.п.)."""
        beam = STT_BEAM if partial else STT_BEAM_FINAL
        prompt = STT_LOOP_PROMPT if self.loopback else STT_MIC_PROMPT
        kwargs = {
            "language": STT_LANGUAGE,
            "beam_size": beam,
            "condition_on_previous_text": False,
            "initial_prompt": prompt or None,
            "temperature": 0.0,
            "no_speech_threshold": STT_NO_SPEECH_THRESHOLD,
            "log_prob_threshold": STT_LOGPROB_THRESHOLD,
            "compression_ratio_threshold": STT_COMPRESSION_THRESHOLD,
            "vad_filter": STT_WHISPER_VAD,
        }
        if STT_WHISPER_VAD:
            kwargs["vad_parameters"] = {"min_silence_duration_ms": STT_VAD_MIN_SILENCE_MS}
        try:
            started = time.perf_counter()
            segments, info = self._model.transcribe(audio, **kwargs)
            segment_list = list(segments)
            decode_sec = time.perf_counter() - started
            text = " ".join(seg.text.strip() for seg in segment_list).strip()
            if text and not partial and self._reject_text(text):
                log.info("STT[%s]: отброшена короткая/шумовая фраза: %r", self.channel, text)
                return ""
            if text:
                log.info("STT[%s] (p=%.2f, beam=%d, decode=%.2fs, vad=%s): %s",
                         self.channel, info.language_probability, beam, decode_sec,
                         "whisper" if STT_WHISPER_VAD else "energy", text)
            return text
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] транскрипция не удалась: %s", exc)
            return ""

    def _reject_text(self, text: str) -> bool:
        """Чистое решение для фильтра финального текста без потери mic-команд."""
        if self._is_hallucination(text):
            return True
        if self._is_repetition(text):
            return True
        norm = self._normalize_text(text)
        if len(norm.replace(" ", "")) < STT_MIN_TEXT_CHARS:
            return True
        if self.loopback:
            words = norm.split()
            if len(norm.replace(" ", "")) < STT_LOOP_MIN_TEXT_CHARS:
                return True
            if len(words) < STT_LOOP_MIN_WORDS:
                return True
        return False

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(text.strip().strip(".!?…-—\"' ").lower().split())

    @staticmethod
    def _is_repetition(text: str) -> bool:
        """«Заезженная пластинка»: whisper на зацикленном шуме повторяет одно
        слово/слог («так так так так…», «да-да-да-да»). Если в достаточно
        длинной фразе доля уникальных слов слишком мала — это не речь."""
        if not STT_REPEAT_FILTER:
            return False
        words = STTModule._normalize_text(text).replace("-", " ").split()
        if len(words) < STT_REPEAT_MIN_WORDS:
            return False
        return len(set(words)) / len(words) <= STT_REPEAT_MAX_UNIQUE_RATIO

    def _is_hallucination(self, text: str) -> bool:
        """Whisper на шуме/тишине выдаёт типовые «титры»-галлюцинации. Если ВСЯ
        фраза (без пунктуации) — одна из них, это не речь пользователя."""
        norm = STTModule._normalize_text(text)
        if any(norm == p or (len(norm) <= len(p) + 4 and p in norm)
               for p in STT_HALLUCINATION_PHRASES):
            return True
        # Эхо initial_prompt (живой тест 13.09: на шуме микрофона при старте
        # whisper выдавал сам текст подсказки «Нима, Нима, Кизилл, Twitch,
        # Dota, стрим…» — она уходила в субтитры как реплика пользователя).
        # Почти все слова фразы есть в словаре подсказки → это эхо, не речь.
        prompt = (STT_LOOP_PROMPT if self.loopback else STT_MIC_PROMPT).lower()
        strip = ".,!?…-—\"'"
        prompt_words = {w.strip(strip) for w in prompt.split() if len(w) > 2}
        text_words = [w.strip(strip) for w in STTModule._normalize_text(text).split()
                      if len(w.strip(strip)) > 2]
        if text_words and prompt_words:
            inside = sum(1 for w in text_words if w in prompt_words)
            if inside / len(text_words) >= 0.8:
                return True
        return False
