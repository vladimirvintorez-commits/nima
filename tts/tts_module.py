"""TTS: синтез с клонированием голоса (Coqui XTTS v2) — стриминговый v14.

Скорость (главное изменение): speak_stream() принимает ГЕНЕРАТОР предложений —
синтез следующего предложения идёт ПОКА играет текущее (producer+player),
поэтому первый звук слышен сразу после первого предложения LLM.

Голос клонируется нулевым выстрелом по референсам из корня проекта
(«Референс 1.wav», «Референс 2.wav»). Модель лениво грузится в фоне (прогрев),
GPU приоритетен: ~2 ГБ VRAM рядом с LLM ~2 ГБ влезает в 6 ГБ.

Банворды: data/speech_banwords.txt (слово на строку) — маскируются в речи
(debug menu → БАНВОРДЫ). Трасса: этапы tts_start/tts_done в pipeline_trace.

Во время воспроизведения модуль считает RMS потока и отдаёт огибающую рта
callback'у on_mouth(0..1) — аватар открывает губы в такт звуку (липсинк).
"""
from __future__ import annotations

import logging
import os
import queue
import re
import threading
import time
import wave
from pathlib import Path

import numpy as np

from core.config import (CACHE_DIR, CHATTERBOX_CFG_WEIGHT,
                         CHATTERBOX_EXAGGERATION, CHATTERBOX_TEMPERATURE,
                         EMOTION_EXAGGERATION, EMOTION_SPEED,
                         SILERO_EMOTION_PITCH, SILERO_EMOTION_RATE,
                         SILERO_PITCH_SHIFT, SILERO_SAMPLE_RATE, SILERO_SPEAKER,
                         SILERO_VERSION, SPEECH_BANWORDS_PATH, TTS_BACKEND,
                         TTS_FIRST_MIN_CHARS, TTS_LANGUAGE, TTS_MAX_CHARS,
                         TTS_MERGE_CHARS, TTS_MONITOR_DEVICE, TTS_OUTPUT_DEVICE,
                         TTS_REFS, TTS_REPETITION_PENALTY, TTS_SPLIT_AT,
                         TTS_TEMPERATURE, TTS_TOP_P)

log = logging.getLogger("tts")

from xml.sax.saxutils import escape as _xml_escape  # noqa: E402  (SSML-текст)

os.environ.setdefault("COQUI_TOS_AGREED", "1")

# Эмодзи, разметка и прочий мусор не едут в синтез
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF\uFE0F\u2764]+")
_MARKUP_RE = re.compile(r"[*_`~#»«\"]+")
_SEARCH_TAG_RE = re.compile(r"\[ПОИСК[^\]]*\]", re.IGNORECASE)
# 3b-модель выдумывает свои теги («[ПОМЯЧАТЬ: лапки]», «[ГЛАЗЫ: лук]») —
# любой скобочный блок целиком вырезается из озвучки
_ANY_TAG_RE = re.compile(r"\[[^\]]{0,60}\]")
# ремарки-сценические действия в круглых скобках («(сначала молчит, потом
# подвывает)») персона запрещает, но 4b их всё равно пишет — из ОЗВУЧКИ
# вырезаем всегда (в субтитрах/памяти текст остаётся)
_STAGE_DIRECTION_RE = re.compile(r"\([^()]{0,120}\)")


def _load_banwords() -> list[re.Pattern]:
    """data/speech_banwords.txt: одно слово/фраза на строку (# — комментарий)."""
    try:
        lines = SPEECH_BANWORDS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    patterns = []
    for line in lines:
        word = line.strip()
        if not word or word.startswith("#"):
            continue
        patterns.append(re.compile(re.escape(word), re.IGNORECASE))
    return patterns


_BANWORDS: list[re.Pattern] | None = None


def clean_for_tts(text: str) -> str:
    global _BANWORDS
    text = _EMOJI_RE.sub(" ", text)
    text = _MARKUP_RE.sub(" ", text)
    text = _SEARCH_TAG_RE.sub(" ", text)  # тег поиска не озвучивается
    text = _ANY_TAG_RE.sub(" ", text)     # и самодельные теги модели тоже
    text = _STAGE_DIRECTION_RE.sub(" ", text)  # ремарки в скобках — тоже
    if _BANWORDS is None:
        _BANWORDS = _load_banwords()
    for pattern in _BANWORDS:  # маскировка: слово глотается мягким «мм»
        text = pattern.sub(" мм ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_sentences(text: str) -> list[str]:
    """Реплика → предложения для стриминговой озвучки."""
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def speech_chunks(sentences) -> list[str]:
    """Предложения → чанки синтеза. Роботизированность XTTS сильно растёт на
    коротких предложениях (просодия обнуляется на границе каждого синтеза), а
    первый звук — от длины первого чанка. Поэтому: короткие склеиваем до
    TTS_MERGE_CHARS, слишком длинный первый чанк режем по запятой."""
    chunks: list[str] = []
    buf = ""
    for sentence in sentences:
        sentence = clean_for_tts(sentence)
        if not sentence:
            continue
        if sentence and len(sentence) > TTS_MAX_CHARS:
            sentence = sentence[:TTS_MAX_CHARS]
        buf = f"{buf} {sentence}".strip() if buf else sentence
        # первый чанк отдаём сразу, как только он достаточно длинный
        if not chunks and len(buf) >= TTS_FIRST_MIN_CHARS:
            while len(buf) >= TTS_SPLIT_AT:      # слишком длинный старт — режем
                piece, buf = _split_at_comma(buf, TTS_SPLIT_AT)
                if piece:
                    chunks.append(piece)
            if buf:
                chunks.append(buf)
                buf = ""
            continue
        if len(buf) >= TTS_MERGE_CHARS:
            while len(buf) >= TTS_MERGE_CHARS + 80:
                piece, buf = _split_at_comma(buf, TTS_MERGE_CHARS + 80)
                if piece:
                    chunks.append(piece)
            if buf:
                chunks.append(buf)
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def _split_at_comma(text: str, limit: int) -> tuple[str, str]:
    """Резать по последней запятой/тире до limit (просодия живее, чем жёсткий срез)."""
    cut = max(text.rfind(",", 40, limit), text.rfind(" — ", 40, limit),
              text.rfind(": ", 40, limit))
    if cut <= 0:
        return text[:limit].rstrip(), text[limit:].strip()
    return text[:cut + 1].strip(), text[cut + 1:].strip()


def _resolve_output_device(name_frag: str) -> int | None:
    """Имя устройства вывода (часть имени, регистр не важен) или номер → id
    для sounddevice. ""/None → None (устройство по умолчанию)."""
    if not name_frag:
        return None
    try:
        import sounddevice as sd
        frag = str(name_frag).strip().lower()
        if frag.isdigit():
            return int(frag)
        for idx, dev in enumerate(sd.query_devices()):
            if dev.get("max_output_channels", 0) > 0 and \
                    frag in str(dev.get("name", "")).lower():
                return idx
    except Exception:  # noqa: BLE001 — звук не критичен для загрузки модуля
        pass
    return None


def _pitch_total(mood: str) -> int:
    """Итоговый сдвиг тона: базовый (пикми) + эмоция, проценты складываются.

    Потолок +35: выше Silero v5 «плывёт» (joy 25+12=+37 — артефакты, v14.3.5).
    """
    total = 0
    for value in (SILERO_PITCH_SHIFT, SILERO_EMOTION_PITCH.get(mood)):
        if value:
            try:
                total += int(value.rstrip("%"))
            except ValueError:
                log.warning("[WARNING] кривой pitch-процент: %r — пропущен", value)
    return max(-40, min(35, total))


class TTSModule:
    def __init__(self, refs: list[Path] | None = None, device: str = "",
                 on_mouth=None, on_start=None, on_end=None) -> None:
        self.refs = [p for p in (refs or TTS_REFS) if Path(p).exists()]
        self.device = device  # "" = авто: cuda → cpu
        self.on_mouth = on_mouth        # callable(amplitude: float)
        self.on_start = on_start
        self.on_end = on_end
        self._tts = None
        self._backend = TTS_BACKEND
        self._device_resolved = ""
        self._ready = threading.Event()
        # Перебивание v14.5: у КАЖДОГО потока озвучки свой stop-флаг. stop()
        # глушит «предыдущий + текущий» (внешняя команда), stop_previous() —
        # только предыдущий (бесшовная передача слова внутри нового потока).
        self._stop_flag = threading.Event()
        self._prev_stop = threading.Event()
        self._synth_lock = threading.Lock()  # синтез строго по одному (краш-урок v13)
        self._streams = 0
        self._streams_lock = threading.Lock()
        self.speaking = False
        # Мониторный дубль: физический endpoint может быть «зомби» (живой тест
        # v14.8.1: после смены версий VoiceMeeter все физические устройства
        # не открываются ни в одном API) — после 3 неудач дубль отключаем
        # до перезапуска, основной вывод голос доносит и так.
        self._mon_open_fails = 0
        self._mon_off = False

    # --- прогрев ---
    def warmup_async(self) -> None:
        threading.Thread(target=self._load, name="TtsWarmup", daemon=True).start()

    def _resolve_device(self) -> str:
        if self.device:
            return self.device
        try:
            import torch
            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"

    def _load(self) -> None:
        if not self.refs:
            log.error("[ERROR] референсы голоса не найдены — клонирование невозможно")
        try:
            import torch
            # benchmark-автотюнинг конволюций в чужом потоке — источник
            # нативных крашей на этой машине (0xC0000005, v11/v13)
            torch.backends.cudnn.benchmark = False
        except Exception:  # noqa: BLE001
            pass
        self._backend = TTS_BACKEND
        if self._backend == "chatterbox":
            try:
                self._load_chatterbox()
                return
            except Exception as exc:  # noqa: BLE001
                log.error("[ERROR] Chatterbox не загрузился (%s) — откат на XTTS v2", exc)
                self._backend = "xtts"
        elif self._backend == "silero":
            try:
                self._load_silero()
                return
            except Exception as exc:  # noqa: BLE001
                log.error("[ERROR] Silero не загрузился (%s) — откат на XTTS v2", exc)
                self._backend = "xtts"
        self._load_xtts()

    def _warm_file(self) -> Path:
        return Path(os.environ.get("TEMP", ".")) / "nima_tts_warm.wav"

    def _load_chatterbox(self) -> None:
        """Chatterbox Multilingual v3: 0.5B (в разы легче XTTS), клон по одному
        рефу, русский из коробки, живая интонация."""
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        t0 = time.time()
        self._device_resolved = self._resolve_device()
        self._tts = ChatterboxMultilingualTTS.from_pretrained(device=self._device_resolved)
        # Кондиционалы голоса считаем ОДИН раз: если передавать референс в
        # generate(), эмбеддинг пересчитывается на каждом чанке (librosa +
        # два энкодера) — это съедало больше секунды на фразу
        self._tts.prepare_conditionals(str(self.refs[0]),
                                       exaggeration=CHATTERBOX_EXAGGERATION)
        # Тёплый синтез В ЭТОМ ЖЕ потоке (гонка CUDA-инициализации — урок v13)
        self._tts.generate("привет", language_id=TTS_LANGUAGE,
                           exaggeration=CHATTERBOX_EXAGGERATION,
                           cfg_weight=CHATTERBOX_CFG_WEIGHT,
                           temperature=CHATTERBOX_TEMPERATURE)
        self._ready.set()
        log.info("Chatterbox Multilingual загружен и прогрет за %.1f с (device=%s, реф: %s)",
                 time.time() - t0, self._device_resolved, self.refs[0].name)

    def _load_silero(self) -> None:
        """Silero (v5_5_ru по умолчанию, фолбэк v4_ru): CPU, RTF ~0.07 —
        мгновенный, чистый русский, SSML-эмоции. Голос из фиксированного
        набора (SILERO_SPEAKER), клона нет."""
        import torch
        t0 = time.time()
        torch.hub.set_dir(str(CACHE_DIR / "torch_hub"))
        version = SILERO_VERSION
        try:
            self._tts, _ = torch.hub.load("snakers4/silero-models", "silero_tts",
                                          language="ru", speaker=version)
        except Exception:  # noqa: BLE001
            log.warning("[WARNING] Silero %s не загрузился — откат на v4_ru", version)
            version = "v4_ru"
            self._tts, _ = torch.hub.load("snakers4/silero-models", "silero_tts",
                                          language="ru", speaker=version)
        self._device_resolved = "cpu"
        # Синтез идёт ПОКА играет звук: Torch по умолчанию ест все ядра и
        # недогружает аудиобуфер sounddevice (треск/«странная акустика»).
        # Оставляем ядра аудиопотоку.
        torch.set_num_threads(max(2, (os.cpu_count() or 4) - 2))
        self._tts.apply_tts(text="привет", speaker=SILERO_SPEAKER,
                            sample_rate=SILERO_SAMPLE_RATE)
        # Прогрев SSML-ветки (эмоции): XML-парсер и prosody-длительности
        self._tts.apply_tts(ssml_text='<speak><prosody rate="fast" pitch="+12%">'
                            'привет</prosody></speak>',
                            speaker=SILERO_SPEAKER, sample_rate=SILERO_SAMPLE_RATE)
        self._ready.set()
        log.info("Silero %s загружен и прогрет за %.1f с (голос: %s, %d Гц, cpu)",
                 version, time.time() - t0, SILERO_SPEAKER, SILERO_SAMPLE_RATE)

    def _load_xtts(self) -> None:
        try:
            from TTS.api import TTS
            t0 = time.time()
            self._device_resolved = self._resolve_device()
            self._tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2",
                            gpu=(self._device_resolved == "cuda"))
            # Тёплый синтез В ЭТОМ ЖЕ потоке: cuDNN инициализируется здесь,
            # до того как worker-пайплайн впервые тронет CUDA (гонка потоков
            # на первой конволюции роняла процесс — cudnnGetLibConfig, code 127)
            self._tts.tts_to_file(text="привет", language=TTS_LANGUAGE,
                                  speaker_wav=[str(r) for r in self.refs],
                                  file_path=str(self._warm_file()))
            self._ready.set()
            log.info("XTTS v2 загружена и прогрета за %.1f с (device=%s, референсов: %d)",
                     time.time() - t0, self._device_resolved, len(self.refs))
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] XTTS не загрузилась: %s", exc)

    # --- синтез ---
    def _synthesize(self, text: str, speed: float = 1.0, mood: str = "normal",
                    stop_flag: threading.Event | None = None) -> Path | None:
        stop_flag = stop_flag or self._stop_flag
        if not self._ready.wait(timeout=300):
            log.warning("[WARNING] TTS так и не прогрелся — фраза пропущена")
            return None
        if self._backend == "chatterbox":
            return self._synthesize_chatterbox(text, mood, stop_flag)
        if self._backend == "silero":
            return self._synthesize_silero(text, speed, mood, stop_flag)
        return self._synthesize_xtts(text, speed, stop_flag)

    def _synthesize_silero(self, text: str, speed: float = 1.0,
                           mood: str = "normal",
                           stop_flag: threading.Event | None = None) -> Path | None:
        stop_flag = stop_flag or self._stop_flag
        out = Path(os.environ.get("TEMP", ".")) / \
            f"nima_tts_{int(time.time() * 1000)}_{threading.get_ident()}.wav"
        try:
            with self._synth_lock:
                if stop_flag.is_set():
                    return None
                # Эмоции нативным SSML-prosody: rate — словесный темп
                # (проценты в rate у v4_ru сломаны, см. config), pitch —
                # сумма глобального сдвига (пикми-режим) и эмоции.
                # Пустой <prosody> запрещён — при 0/None ставим хотя бы rate.
                rate = SILERO_EMOTION_RATE.get(mood)
                total_pitch = _pitch_total(mood)
                attrs = ""
                if rate:
                    attrs += f' rate="{rate}"'
                if total_pitch:
                    attrs += f' pitch="{total_pitch:+d}%"'
                spoken = (f"<prosody{attrs}>{_xml_escape(text)}</prosody>"
                          if attrs else _xml_escape(text))
                try:
                    audio = self._tts.apply_tts(
                        ssml_text=f"<speak>{spoken}</speak>",
                        speaker=SILERO_SPEAKER, sample_rate=SILERO_SAMPLE_RATE)
                except Exception as ssml_exc:
                    # живой тест v14.6: парсер SSML Silero срывается на
                    # отдельных чанках ('NoneType' object has no attribute
                    # 'keys') — чанк не должен ронять фразу, пробуем без prosody
                    log.warning("[WARNING] SSML-синтез (%s) — фолбэк без prosody",
                                ssml_exc)
                    if stop_flag.is_set():
                        return None
                    audio = self._tts.apply_tts(
                        text=text, speaker=SILERO_SPEAKER,
                        sample_rate=SILERO_SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] синтез Silero не удался: %s", exc)
            return None
        audio = audio.detach().cpu().numpy().astype(np.float32)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16)  # моно int16
        try:
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SILERO_SAMPLE_RATE)
                w.writeframes(pcm.tobytes())
        except OSError as exc:
            log.error("[ERROR] запись wav Silero: %s", exc)
            return None
        return out
    def _synthesize_chatterbox(self, text: str, mood: str = "normal",
                               stop_flag: threading.Event | None = None) -> Path | None:
        stop_flag = stop_flag or self._stop_flag
        out = Path(os.environ.get("TEMP", ".")) / \
            f"nima_tts_{int(time.time() * 1000)}_{threading.get_ident()}.wav"
        exg = EMOTION_EXAGGERATION.get(mood) or CHATTERBOX_EXAGGERATION
        try:
            with self._synth_lock:
                if stop_flag.is_set():
                    return None
                # без audio_prompt_path: кондиционалы уже готовы (см. _load_chatterbox)
                wav = self._tts.generate(
                    text, language_id=TTS_LANGUAGE, exaggeration=exg,
                    cfg_weight=CHATTERBOX_CFG_WEIGHT,
                    temperature=CHATTERBOX_TEMPERATURE)
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] синтез Chatterbox не удался: %s", exc)
            return None
        audio = wav.squeeze(0).detach().cpu().numpy().astype(np.float32)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1.0:
            audio /= peak
        pcm = (audio * 32767.0).astype(np.int16)   # 24 кГц моно — как у XTTS
        try:
            import wave as _wave
            with _wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm.tobytes())
        except OSError as exc:
            log.error("[ERROR] запись wav Chatterbox: %s", exc)
            return None
        return out

    def _synthesize_xtts(self, text: str, speed: float = 1.0,
                         stop_flag: threading.Event | None = None) -> Path | None:
        stop_flag = stop_flag or self._stop_flag
        out = Path(os.environ.get("TEMP", ".")) / \
            f"nima_tts_{int(time.time() * 1000)}_{threading.get_ident()}.wav"
        # Чанки уже нарезаны снаружи: внутренний сплиттер XTTS выключен —
        # он резал посреди фраз и давал артефакты просодии
        kwargs = dict(
            text=text, language=TTS_LANGUAGE,
            speaker_wav=[str(r) for r in self.refs],
            file_path=str(out),
            enable_text_splitting=False,
            temperature=TTS_TEMPERATURE, top_p=TTS_TOP_P,
            repetition_penalty=TTS_REPETITION_PENALTY,
            speed=speed,
        )
        try:
            with self._synth_lock:
                if stop_flag.is_set():
                    return None
                try:
                    self._tts.tts_to_file(**kwargs)
                except TypeError:
                    # старая версия TTS без speed/сэмплинг-параметров
                    for key in ("speed", "repetition_penalty", "top_p", "temperature",
                                "enable_text_splitting"):
                        kwargs.pop(key, None)
                    self._tts.tts_to_file(**kwargs)
            return out
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] синтез не удался: %s", exc)
            return None

    # --- стриминговая озвучка: синтез N+1 пока играет N ---
    def speak_stream(self, sentences, trace=None, request_id: str | None = None,
                     mood_box=None, pre_play=None, on_sentence=None) -> bool:
        """Озвучка генератора предложений. Блокирует до конца (worker-поток).

        mood_box: изменяемый контейнер эмоции (mood_box[0] = 'joy'|'anger'|…) —
        скорость/темп речи на каждом чанке берутся из АКТУАЛЬНОГО настроения.
        trace: модуль core/pipeline_trace (опционально) — этапы tts_start/tts_done.
        pre_play: callable — вызывается РОВНО перед первым воспроизведением.
        on_sentence: callable(text) — вызывается РОВНО перед воспроизведением
        КАЖДОГО чанка (v14.7.2). Нужно для субтитров: раньше субтитр ставился в
        producer'е пайплайна (текст только сгенерирован, ещё в очереди синтеза) →
        караоке-строка вылезала блоком и ДО голоса. Теперь субтитр рисуется
        синхронно с началом озвучки чанка — что звучит, то и на экране.
        Бесшовное перебивание (v14.5): новый ответ синтезируется, ПОКА ещё
        играет старая речь; pre_play глушит её — и первый звук новой реплики
        идёт сразу, без мёртвой тишины на генерацию. False — не сыграли ничего."""
        self._prev_stop = self._stop_flag    # предыдущий поток, если ещё играет
        stop_flag = threading.Event()
        self._stop_flag = stop_flag
        chunks = speech_chunks(sentences)
        if not chunks:
            return False

        synth_q: queue.Queue = queue.Queue(maxsize=4)

        def producer():
            for chunk in chunks:
                if stop_flag.is_set():
                    break
                mood = mood_box[0] if mood_box else "normal"
                wav = self._synthesize(chunk, speed=EMOTION_SPEED.get(mood, 1.0),
                                       mood=mood, stop_flag=stop_flag)
                if wav:
                    if trace is not None and synth_q.empty():
                        trace.mark("tts_synth_done", request_id, text=chunk)
                    synth_q.put((chunk, wav))
            synth_q.put(None)

        threading.Thread(target=producer, name="TtsSynth", daemon=True).start()

        with self._streams_lock:
            self._streams += 1
            self.speaking = True
        if self.on_start:
            try:
                self.on_start()
            except Exception:  # noqa: BLE001
                pass
        played = False
        try:
            while not stop_flag.is_set():
                item = synth_q.get()
                if item is None:
                    break
                sentence, wav = item
                if not played:
                    if pre_play:
                        try:
                            pre_play()   # глушит ПРЕДЫДУЩУЮ речь, не эту
                        except Exception:  # noqa: BLE001
                            pass
                    if trace is not None:
                        trace.mark("tts_start", request_id, text=sentence)
                # субтитр КАЖДОГО чанка — ровно перед его воспроизведением, чтобы
                # строка на экране совпадала с тем, что звучит (v14.7.2)
                if on_sentence:
                    try:
                        on_sentence(sentence)
                    except Exception:  # noqa: BLE001
                        pass
                def on_audio_start():
                    if trace is not None and not played:
                        trace.mark("audio_start", request_id, text=sentence)

                if self._play(wav, stop_flag, on_audio_start=on_audio_start):
                    played = True
                try:
                    wav.unlink(missing_ok=True)
                except OSError:
                    pass
        finally:
            with self._streams_lock:
                self._streams -= 1
                last = self._streams <= 0
                if last:
                    self.speaking = False
            if last:   # заглушённый новым поток не трогает рот/колбэки
                if self.on_mouth:
                    try:
                        self.on_mouth(0.0)
                    except Exception:  # noqa: BLE001
                        pass
                if self.on_end:
                    try:
                        self.on_end()
                    except Exception:  # noqa: BLE001
                        pass
                if trace is not None and played:
                    trace.mark("tts_done", request_id)
        return played

    def stop(self) -> None:
        """Заглушить всё: и предыдущий поток, и текущий (внешние команды)."""
        self._prev_stop.set()
        self._stop_flag.set()

    def stop_previous(self) -> None:
        """Заглушить только ПРЕДЫДУЩИЙ поток (вызывается из pre_play нового)."""
        self._prev_stop.set()

    def active_streams(self) -> int:
        with self._streams_lock:
            return self._streams

    def speak(self, text: str, trace=None, request_id: str | None = None) -> bool:
        """Блокирующая озвучка одного текста (вызывать из worker-потока)."""
        return self.speak_stream(iter([text]), trace=trace, request_id=request_id)

    def stop(self) -> None:
        self._stop_flag.set()

    def _play(self, wav_path: Path, stop_flag: threading.Event | None = None,
              on_audio_start=None) -> bool:
        """Воспроизведение. TTS_OUTPUT_DEVICE — куда играть (по умолчанию —
        в устройство по умолчанию), TTS_MONITOR_DEVICE — параллельный дубль
        себе в наушники (сценарий «голос уходит в виртуальный кабель, а
        владелец слышит его в наушниках»). stop_flag — свой у каждого потока
        озвучки (бесшовное перебивание)."""
        import sounddevice as sd

        try:
            with wave.open(str(wav_path), "rb") as w:
                sr, nch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
                audio = w.readframes(w.getnframes())
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] wav не читается: %s", exc)
            return False
        dtype = {1: "int8", 2: "int16", 4: "int32"}[sw]
        audio = np.frombuffer(audio, dtype=dtype).reshape(-1, nch)

        outs = []
        main_dev = _resolve_output_device(TTS_OUTPUT_DEVICE)
        mon_dev = _resolve_output_device(TTS_MONITOR_DEVICE)
        if TTS_OUTPUT_DEVICE and main_dev is None:
            log.warning("[WARNING] устройство вывода %r не найдено — играю "
                        "в устройство по умолчанию", TTS_OUTPUT_DEVICE)
        streams: list[sd.OutputStream] = []
        try:
            targets = [main_dev]                       # None = устройство по умолчанию
            if self._mon_off:
                mon_dev = None
            if mon_dev is not None and mon_dev != main_dev:
                targets.append(mon_dev)                # дубль себе в наушники
            mon_failed = False
            for dev in targets:
                kwargs = dict(samplerate=sr, channels=nch, dtype=dtype)
                if dev is not None:
                    kwargs["device"] = dev
                try:
                    streams.append(sd.OutputStream(**kwargs))
                except Exception as exc:  # noqa: BLE001
                    log.error("[ERROR] OutputStream (%s) не открылся: %s",
                              dev, exc)
                    if dev == mon_dev:
                        mon_failed = True
            # Зомби-endpoint не лечится повторами: после 3 неудач подряд
            # мониторный дубль выключаем до перезапуска (основной вывод
            # доносит голос; дубль — только комфортное дополнение).
            if mon_failed:
                self._mon_open_fails += 1
                if self._mon_open_fails >= 3:
                    self._mon_off = True
                    log.warning("[WARNING] монитор %r не открывается "
                                "(%d попыток) — отключаю дубль до перезапуска",
                                TTS_MONITOR_DEVICE, self._mon_open_fails)
            elif mon_dev is not None:
                self._mon_open_fails = 0
            # Настроенное устройство не открылось, но дефолт ещё не пробовали
            # (main_dev был задан явно) — пробуем дефолт как последний фолбэк.
            if not streams and main_dev is not None:
                try:
                    streams.append(sd.OutputStream(samplerate=sr, channels=nch,
                                                   dtype=dtype))
                    log.warning("[WARNING] играю в устройство по умолчанию "
                                "(настроенное недоступно)")
                except Exception as exc:  # noqa: BLE001
                    log.error("[ERROR] дефолтный OutputStream не открылся: %s", exc)
            if not streams:
                # Ни одно аудиоустройство недоступно (напр. headless/нет звука).
                # Это НЕ провал реплики: деградируем тихо — реплика логически
                # «прозвучала», пайплайн/субтитры не залипают на отсутствии звука.
                log.warning("[WARNING] нет доступного аудиовыхода — реплика без звука")
                if on_audio_start:
                    on_audio_start()
                return True
            for st in streams:
                st.start()
            if on_audio_start:
                on_audio_start()

            chunk = sr // 10  # по 100 мс
            for i in range(0, len(audio), chunk):
                if stop_flag is not None and stop_flag.is_set():
                    break
                piece = audio[i:i + chunk]
                for st in streams:
                    st.write(piece)
                if self.on_mouth:
                    mono = piece.astype(np.float32) / 32768.0
                    amp = float(np.sqrt(np.mean(mono * mono)))
                    try:
                        self.on_mouth(min(1.0, amp * 6.0))
                    except Exception:  # noqa: BLE001
                        pass
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] воспроизведение не удалось: %s", exc)
            return False
        finally:
            for st in streams:
                try:
                    st.stop()
                    st.close()
                except Exception:  # noqa: BLE001
                    pass
