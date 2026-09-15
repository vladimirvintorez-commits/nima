"""start.py — оркестратор Нимфеи (composition root, v14).

Запускается debug menu (КНОПКА «ЗАПУСТИТЬ НИМФЕЮ» → runtime_console).
Здесь ТОЛЬКО сборка и связывание модулей — логика живёт в папках-модулях:

    core/      инфраструктура (config, лог, события, трасса)
    memory/    память → memory.json (корень проекта) + голосовые профили
    prompts/   построитель промптов + персона
    llm/       LLM: Ollama + дообученная models/nimfea-q4_k_m.gguf
    stt/       распознавание: faster-whisper, CPU — микрофон + LOOPBACK (созвон)
    ears/      гейт адресации: слышит всегда, отвечает по имени
    voice_id/  кто говорит: идентификация по голосу (имена собеседников)
    tts/       озвучка: XTTS v2 стримом, клонирование по референсам
    web/       интернет-поиск ([ПОИСК] + нерешённые вопросы)
    pipeline/  диалоговый конвейер + нити разговора + ручной ввод
    initiative/ живая инициатива (сама заводит разговоры)
    avatar/    прозрачное Electron-окно с VRM (+ мост, VRMA-анимации)
    twitch/    чат стримерши (опционально)
"""
from __future__ import annotations

import logging
import threading
import time

from core import config
from core.config import ensure_dirs
from core.events import EventBus
from core.logging_setup import setup_logging

setup_logging()
log = logging.getLogger("start")

VERSION = "v14.8.52"

# Эксклюзивный lock единственного экземпляра (живой тест v14.8.2: повторная
# кнопка «ЗАПУСТИТЬ» поверх работающей Нимфеи = второй оркестратор и ВТОРОЕ
# окно Electron-аватара, плюс война двух процессов за микрофон/звук).
# Держим дескриптор открытым: умирает процесс — ОС снимает блокировку сама,
# никакой уборки «мёртвых» lock-файлов не нужно.
_INSTANCE_LOCK = None


def _acquire_instance_lock() -> bool:
    """True — мы единственный экземпляр; False — Нимфея уже работает.
    «Замок» — байт 0 пустого lock-файла (msvcrt-блокировка региона; умирает
    процесс — ОС снимает её сама, уборка мёртвых lock-файлов не нужна).
    ВАЖНО: в файл ничего не пишем — залоченный регион запрещает запись в него
    ЛЮБОМУ процессу, включая владельца (WriteFile → ERROR_LOCK_VIOLATION)."""
    global _INSTANCE_LOCK
    import msvcrt
    from pathlib import Path

    lock_path = Path(__file__).parent / "logs" / "nima_instance.lock"
    lock_path.parent.mkdir(exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        return False
    _INSTANCE_LOCK = handle  # живёт до конца процесса — блокировка держится
    return True


def build_system() -> dict:
    """Собирает систему из модулей. Каждый модуль изолирован: тут только
    импорт, конструкторы и проводка колбэков."""
    from avatar.bridge import AvatarBridge
    from diary.diary_module import DiaryModule
    from ears.ears_module import is_addressed  # noqa: F401 (использует pipeline)
    from initiative.initiative_module import InitiativeModule
    from llm.llm_module import LLMModule
    from memory.memory_module import MemoryModule
    from pipeline import manual_control
    from pipeline.pipeline import DialoguePipeline
    from prompts.prompt_builder import PromptBuilder
    from stt.stt_module import STTModule
    from tts.tts_module import TTSModule
    from twitch.twitch_module import TwitchModule
    from vision.vision_module import VisionModule
    from voice_id.voice_id_module import VoiceID
    from watchdog.watchdog_module import WatchdogModule
    from web.web_module import search
    from types import SimpleNamespace

    events = EventBus()
    modules = {}

    modules["memory"] = MemoryModule()
    modules["prompts"] = PromptBuilder(modules["memory"])
    modules["llm"] = LLMModule()
    modules["avatar"] = AvatarBridge()
    modules["tts"] = TTSModule(on_mouth=modules["avatar"].set_mouth)
    modules["web"] = SimpleNamespace(search=search)
    modules["initiative"] = InitiativeModule(modules["memory"], modules["web"])
    # зрение: та же gemma3:4b (мультимодальная) — второй VLM не заводим
    modules["vision"] = VisionModule(modules["memory"], modules["llm"])
    modules["pipeline"] = DialoguePipeline(
        memory=modules["memory"], prompts=modules["prompts"], llm=modules["llm"],
        tts=modules["tts"], avatar=modules["avatar"], events=events,
        web=modules["web"], initiative=modules["initiative"],
        vision=modules["vision"])
    modules["voice_id"] = VoiceID(modules["memory"])
    modules["pipeline"].voice_id = modules["voice_id"]  # enroll-команды debug menu
    # дневник: ночью сжимает день в воспоминание (факт «Дневник за …»)
    modules["diary"] = DiaryModule(modules["memory"], modules["llm"])
    # сторожевой пёс: умирает тихо — не должно быть никого, кто молчит навсегда
    modules["watchdog"] = WatchdogModule()

    def on_voice(text: str, channel: str, audio, request_id: str | None = None) -> None:
        """Любая голосовая фраза: кто сказал (voice_id) → гейт → пайплайн.
        Пока Нимфея говорит — микрофонный канал работает в режиме перебивания."""
        try:
            speaker = modules["voice_id"].identify(audio, channel)
        except Exception:  # noqa: BLE001 — идентификация не должна ронять слух
            speaker = None
        if modules["tts"].speaking and channel == "mic":
            modules["pipeline"].handle_barge_in(text, channel, speaker=speaker)
            return
        modules["pipeline"].handle_user_text(text, source=channel, speaker=speaker,
                                             request_id=request_id)

    def on_partial(text: str) -> None:
        """Недоговорённая фраза: «стоп» глушит речь мгновенно, не дожидаясь конца."""
        modules["pipeline"].handle_barge_partial(text)

    # STT: у микрофона своя модель; loopback по умолчанию тоже своя (вторая) —
    # иначе звуки с компа заставляют микрофон ждать в очереди (урок v13).
    modules["stt"] = STTModule(on_voice, channel="mic", on_partial=on_partial,
                               device_name=config.STT_MIC_DEVICE)
    modules["stt_loop"] = STTModule(
        on_voice, channel="loop", loopback=config.STT_LOOPBACK,
        rms_threshold=config.STT_LOOPBACK_RMS, device_name=config.STT_LOOPBACK_DEVICE,
        shared_model=modules["stt"]._model if config.STT_LOOPBACK_SHARED else None)
    modules["pipeline"].stt = modules["stt"]          # циклическая связь: пайплайн глушит микрофон
    modules["pipeline"].stt_loop = modules["stt_loop"]

    modules["twitch"] = TwitchModule(on_message=lambda nick, text: modules["pipeline"].handle_user_text(
        f"Сообщение из чата от {nick}: {text}", "twitch"))
    modules["manual_control"] = manual_control
    modules["events"] = events
    return modules


def register_watchdogs(watchdog, stt, stt_loop, llm) -> None:
    """Проверки сторожевого пса: мёртвые STT-потоки перезапускаются,
    недоступная Ollama фиксируется в журнале (ответит сама, когда поднимется)."""

    def _thread_check(stt_mod: "STTModule", label: str):
        def check():
            dead = [name for name, th in
                    (("capture", stt_mod._thread), ("recognizer", stt_mod._recognizer))
                    if th is not None and not th.is_alive()]
            if not dead:
                return None
            return (f"STT[{label}]: поток(и) {','.join(dead)} умерли",
                    lambda: stt_mod.restart())
        return check

    watchdog.register("stt-mic", _thread_check(stt, "mic"))
    if stt_loop.loopback:
        watchdog.register("stt-loop", _thread_check(stt_loop, "loop"))
    # Ollama пёс опрашивает сам (раз в WATCHDOG_OLLAMA_INTERVAL_SEC)


def main() -> None:
    ensure_dirs()
    log.info("════ НИМФЕЯ %s — старт оркестратора ════", VERSION)
    t0 = time.time()

    modules = build_system()
    memory: "MemoryModule" = modules["memory"]
    avatar = modules["avatar"]
    llm = modules["llm"]
    tts = modules["tts"]
    stt = modules["stt"]
    stt_loop = modules["stt_loop"]
    pipeline = modules["pipeline"]
    twitch = modules["twitch"]
    initiative = modules["initiative"]

    log.info("Память: %s | голоса: %d", memory.snapshot_stats(),
             len(memory.people_with_embeddings()))

    # Порядок загрузки нативных библиотек СТРОГИЙ (урок v13: параллельная
    # инициализация CUDA/DLL из разных потоков роняла процесс): аватар
    # (отдельный процесс) → LLM (Ollama, отдельный процесс) → STT (модели
    # синхронно, канал за каналом) → TTS (фоном, с тёплым синтезом в своём потоке).
    avatar.start()
    avatar.command_avatar(action="greeting")  # вышла на стрим — помахала

    # LLM: прогрев с ретраями (гонка VRAM на старте Ollama — урок v11)
    if llm.is_alive():
        llm.warmup()
    else:
        log.error("[ERROR] Ollama недоступна (%s) или модель %s не создана — "
                  "см. models/README.md", config.OLLAMA_URL, config.LLM_MODEL)

    stt.start()
    if config.STT_LOOPBACK:
        stt_loop.start()  # своя модель (или общая при NIMA_STT_LOOPBACK_SHARED=1)
    tts.warmup_async()
    twitch.start()
    initiative.start()
    pipeline.threads.start()
    modules["vision"].start()
    modules["diary"].start()
    register_watchdogs(modules["watchdog"], stt, stt_loop, llm)
    modules["watchdog"].start()
    # глобальные хоткеи: D — глянуть на экран, M — мьют ушей, W — режим
    # просмотра, S — замолчать (работают из любого приложения)
    if config.HOTKEYS_ENABLED:
        import core.hotkeys as hotkeys_mod
        hotkeys = hotkeys_mod.HotkeyThread(pipeline.hotkey_action)
        hotkeys.start()
        modules["hotkeys"] = hotkeys
    try:
        from twitch.twitch_donations import start_http_receiver
        start_http_receiver()
    except Exception:  # noqa: BLE001 — приёмник донатов не критичен
        log.exception("[ERROR] приёмник донатов не поднялся")

    log.info("Система собрана за %.1f с — слушаю микрофон%s",
             time.time() - t0, " + loopback" if config.STT_LOOPBACK else "")
    log.info("Остановка: debug menu → ВЫКЛЮЧИТЬ ПРОЕКТ (или Ctrl+C здесь)")

    # главный цикл: ручной ввод debug menu + ящик донатов + здоровье LLM
    manual = modules["manual_control"]
    try:
        while True:
            pipeline.poll_manual_queue(manual)
            pipeline.poll_donations(config.DONATIONS_INBOX_PATH)
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Остановка системы…")
        modules["vision"].stop()
        modules["diary"].stop()
        modules["watchdog"].stop()
        pipeline.threads.stop()
        initiative.stop()
        pipeline.shutdown()
        stt.stop()
        if config.STT_LOOPBACK:
            stt_loop.stop()
        twitch.stop()
        avatar.stop()
        log.info("════ НИМФЕЯ остановлена ════")


if __name__ == "__main__":
    if not _acquire_instance_lock():
        log.error("[ERROR] Нимфея уже запущена — второй экземпляр отклонён "
                  "(два запуска = два окна аватара и конфликт аудио). "
                  "Выключи текущую: debug menu → ВЫКЛЮЧИТЬ ПРОЕКТ.")
    else:
        main()
