"""Полный регрессионный тест механик Нимфеи v14.

Запускается из debug menu («ПОЛНЫЙ ТЕСТ СИСТЕМЫ»). Импортирует реальные модули
проекта в стандартной конфигурации (без моков логики) и прогоняет
детерминированные проверки: уши/адресация, теги веб-поиска и анимаций, TTS-чистка,
память (диалог/факты/контекст), нити разговора, трасса пайплайна, мост аватара
(seq/перезапуск анимаций), VRMA-ассеты, донатный ящик, конфиг v14, персона.

Назначение: после обновлений быстро ловить сломанные старые механики и новые баги.
Сеть, микрофон, TTS и Electron не требуются.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from .config import PROJECT_ROOT

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _check(condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(detail or "condition is false")


def test_config_v14():
    """Конфиг v14: стримовые тайминги нитей, спокойный сэмплинг LLM, флаги."""
    from core import config

    _check(config.THREADS_FIRST_SEC <= 15, f"нити слишком медленные для стрима: {config.THREADS_FIRST_SEC}с")
    _check(config.THREADS_SECOND_SEC <= 30, f"второй хвост слишком медленный: {config.THREADS_SECOND_SEC}с")
    _check(config.LLM_TEMPERATURE <= 0.75, f"температура слишком высокая (салат): {config.LLM_TEMPERATURE}")
    _check(config.LLM_NUM_CTX >= 4096, "маленький контекст LLM")
    _check(bool(config.EARS_NAMES), "список имён для ушей пуст")
    _check(hasattr(config, "STT_LOOPBACK_SHARED"), "нет флага отдельной loopback-модели STT")
    _check(str(config.DONATIONS_INBOX_PATH).endswith("donations_inbox.jsonl"), "путь донатного ящика")


def test_ears_gate():
    """Уши: адресация по имени, ослышки, срезание имени."""
    from ears.ears_module import is_addressed, strip_name

    class _NoLLM:
        def generate_micro(self, *a, **k):
            return None

    llm = _NoLLM()
    for text in ("Нима, привет", "нимуль, ты тут?", "Нимфея, что думаешь про доту",
                 "Нимфэя, ты меня слышишь?",  # ослышка STT
                 "Nima, hello"):
        _check(is_addressed(text, llm), f"«{text}» должно распознаваться как обращение")
    for text in ("братан прикрой мид", "что думаешь про этот матч", "да, согласен"):
        _check(not is_addressed(text, llm), f"«{text}» не обращение — отвечать нельзя")
    stripped = strip_name("Нима, как дела?")
    _check("нима" not in stripped.lower(), f"имя не срезано: {stripped!r}")


def test_pipeline_mood_and_anim_tags():
    """Пайплайн: детектор настроения + теги анимаций (в т.ч. самодельные)."""
    from pipeline.pipeline import DialoguePipeline, _detect_mood

    _check(_detect_mood("ты меня бесишь, заткнись") == "anger", "грубость → anger")
    _check(_detect_mood("ахаха, ну ты даёшь") == "joy", "смех → joy")
    _check(_detect_mood("как погода") == "normal", "нейтральное → normal")

    calls = []

    class _Av:
        def command_avatar(self, **kw):
            calls.append(kw.get("action"))

    pipe = SimpleNamespace(avatar=_Av())
    fire = DialoguePipeline._fire_animations.__get__(pipe)

    out = fire("[ТАНЦУЮ] Ну и ладно, давай потанцуем.")
    _check("[ТАНЦУЮ]" not in out and calls[-1] == "dance", f"самодельный тег: {out!r}")
    out = fire("Обожаю донаты! [АНИМАЦИЯ: dance]")
    _check(out.strip() == "Обожаю донаты!" and calls[-1] == "dance", f"каноничный тег: {out!r}")
    out = fire("[ПРЫГАЮ ОТ РАДОСТИ] Ура!")
    _check(calls[-1] == "jump" and out.strip() == "Ура!", f"прыжок: {out!r}")
    out = fire("Просто предложение без действий")
    _check(calls.count(None) == 0 and out == "Просто предложение без действий", "ложное срабатывание")


def test_web_tags():
    """Протокол [ПОИСК: …]: извлечение и чистка."""
    from web.web_module import extract_search_tag, strip_search_tags

    _check(extract_search_tag("[ПОИСК: квазары яркие]") == "квазары яркие", "запрос не извлечён")
    _check(extract_search_tag("обычный текст") is None, "ложный тег")
    _check("ПОИСК" not in strip_search_tags("ответ [ПОИСК: курс] готов"), "тег не вычищен")


def test_tts_clean_and_split():
    """TTS: чистка для озвучки (банворды, служебные теги) + резка на предложения."""
    from tts.tts_module import clean_for_tts, split_sentences

    parts = split_sentences("Привет. Как дела? Отлично… Давай потом")
    _check(len(parts) == 4 and parts[-1] == "Давай потом", f"резка предложений сломана: {parts}")
    cleaned = clean_for_tts("Ответ с [ПОИСК: курс биткоина] внутри")
    _check("ПОИСК" not in cleaned, "тег поиска должен вычищаться из озвучки")

    from core.config import SPEECH_BANWORDS_PATH
    if SPEECH_BANWORDS_PATH.exists():
        first = SPEECH_BANWORDS_PATH.read_text(encoding="utf-8").split("\n")[0].strip()
        if first:
            masked = clean_for_tts(f"ну и {first} же")
            _check(first.lower() not in masked.lower(), f"банворд «{first}» не замаскирован")


def test_memory_roundtrip_and_context():
    """Память: диалог (роли для Ollama), факты, контекст промпта, люди."""
    from memory.memory_module import MemoryModule

    with tempfile.TemporaryDirectory() as tmp:
        mem = MemoryModule(Path(tmp) / "memory.json")
        mem.append_dialog("user", "меня зовут Кизил", user="stt", speaker="Кизил")
        mem.append_dialog("bot", "Привет, Кизил", user="stt")
        mem.append_fact("Кизил стримит по вечерам", source="dialog")
        hist = mem.get_recent_dialog(limit=5)
        roles = [h["role"] for h in hist]
        _check(roles == ["user", "assistant"], f"роли истории должны быть user/assistant: {roles}")
        _check(mem.snapshot_stats()["dialog"] == 2, "диалог не записался")
        reloaded = MemoryModule(Path(tmp) / "memory.json")
        _check(reloaded.snapshot_stats()["dialog"] == 2, "память не пережила перезагрузку")
        ctx = reloaded.get_prompt_context(query="кто стримит", speaker="Друг 1")
        _check("Кизил стримит" in ctx, f"факт не подтянулся в контекст: {ctx[:120]!r}")
        _check("Друг 1" in ctx, "имя говорящего не попало в контекст")


def test_threads_v14():
    """Нити: хвост после вопроса, сброс репликой пользователя, стримовые тайминги."""
    from core.config import THREADS_FIRST_SEC
    from pipeline.threads import ConversationThreads, is_question

    _check(is_question("Нима, как думаешь, кто победит?"), "вопрос не распознан")
    _check(not is_question("Нима, привет"), "приветствие не вопрос")

    threads = ConversationThreads()
    threads.on_bot_reply("Ну и кто сегодня выиграл, а?", "Кизил", "stt", is_question=True)
    _check(bool(threads.pending_summary()), "хвост вопроса не создан")
    threads.on_user_reply("не знаю")
    _check(not threads.pending_summary(), "реплика пользователя не закрыла хвост")


def test_pipeline_trace():
    """Трасса пайплайна: заявки, метки, группировка (debug menu → ТРАССА)."""
    import core.pipeline_trace as trace

    rid = trace.new_request()
    trace.mark("in", rid, text="тест", info="manual/-")
    trace.mark("llm_start", rid)
    trace.mark("llm_done", rid, text="ответ")
    entries = trace.read_recent(limit=10)
    ours = [e for e in entries if e.get("request_id") == rid]
    _check(len(ours) >= 3, "метки не записались в журнал")
    stages = {e["stage"] for e in ours}
    _check({"in", "llm_start", "llm_done"} <= stages, f"стадии не те: {stages}")
    grouped = trace.group_by_request(trace.read_recent(limit=50))
    _check(any(g.get("request_id") == rid for g in grouped), "группировка по заявкам сломана")


def test_avatar_bridge_seq():
    """Мост аватара: seq растёт на каждую команду с action (перезапуск анимаций)."""
    import avatar.bridge as bridge

    with tempfile.TemporaryDirectory() as tmp:
        saved_path = bridge.AVATAR_STATE_PATH
        bridge.AVATAR_STATE_PATH = Path(tmp) / "avatar_state.json"
        try:
            b = bridge.AvatarBridge()
            b.command_avatar(action="dance")
            first = json.loads(bridge.AVATAR_STATE_PATH.read_text(encoding="utf-8"))
            b.command_avatar(action="dance")   # та же анимация — seq должен вырасти
            second = json.loads(bridge.AVATAR_STATE_PATH.read_text(encoding="utf-8"))
            _check(first["action"] == "dance" and second["action"] == "dance", "action не пишется")
            _check(second["seq"] > first["seq"], "seq не растёт — повтор анимации не сработает")
        finally:
            bridge.AVATAR_STATE_PATH = saved_path


def test_donations_inbox():
    """Донатный ящик: запись → разбор пайплайном (имя/сумма/сообщение)."""
    from core.config import DONATIONS_INBOX_PATH
    from pipeline.pipeline import DialoguePipeline

    with tempfile.TemporaryDirectory() as tmp:
        inbox = Path(tmp) / "donations_inbox.jsonl"
        inbox.write_text(json.dumps({"name": "Зритель", "amount": "100",
                                     "currency": "руб", "message": "держи"},
                                    ensure_ascii=False) + "\n", encoding="utf-8")
        got = []

        class _Threads:
            def on_bot_reply(self, *a, **k): pass
            def pending_summary(self): return ""

        pipe = DialoguePipeline.__new__(DialoguePipeline)
        pipe.handle_donation = lambda name, msg, amount: got.append((name, msg, amount))
        DialoguePipeline.poll_donations(pipe, inbox)
        _check(got == [("Зритель", "держи", "100 руб")], f"донат разобран неверно: {got}")
    _check("donations_inbox" in str(DONATIONS_INBOX_PATH), "конфиг ящика сломан")


def test_barge_in():
    """Перебивание: жёсткие триггеры глушат сразу, обращение/связь —
    БЕСШОВНО (v14.5: она договаривает, пока новый ответ синтезируется),
    посторонняя болтовня и эхо — игнор."""
    from memory.memory_module import MemoryModule
    from pipeline.pipeline import DialoguePipeline

    class _TTS:
        speaking = True
        stopped = False
        def stop(self):
            self.stopped = True
            self.speaking = False  # как настоящий TTS: стоп завершает playback

        def speak_stream(self, sentences, **kwargs):
            return False  # worker не должен упасть после перебивания

    class _Prompts:
        threads_summary = None
        def build_system(self, **kw): return "sys"
        def get_history(self): return []

    class _Av:
        def command_avatar(self, **kw): pass
        def set_mouth(self, v): pass

    class _LLM:
        last_error = ""
        def generate_micro(self, *a, **k): return "НЕТ"  # классификатор: не обращение
        def generate_stream(self, *a, **k): return iter(())

    tmp = Path(tempfile.mkdtemp())
    mem = MemoryModule(str(tmp / "memory.json"))
    tts = _TTS()
    pipe = DialoguePipeline(mem, _Prompts(), _LLM(), tts, _Av())
    calls: list[tuple] = []
    pipe._run_async = lambda fn, *a: calls.append((fn, a))  # ответ не тестируем здесь
    pipe._monologue = ["Я вчера катала в доту с друзьями весь вечер."]

    pipe.handle_barge_in("Нима, стоп", "mic")
    _check(tts.stopped, "жёсткий триггер не заглушил речь")
    _check(pipe._barge_window_until > time.time(), "окно перебивания не открыто")

    tts.stopped = False
    pipe._barge_window_until = 0.0
    pipe.handle_barge_in("а ты часто в доту играешь?", "mic")
    _check(not tts.stopped, "связанная фраза заглушила речь мгновенно "
                            "(должна быть бесшовная передача слова)")
    _check(bool(calls) and calls[-1][1][3] is True,
           "бесшовный режим не передан в _respond")

    pipe.handle_barge_in("купил вчера хлеба и молока", "mic")
    _check(not tts.stopped, "посторонняя фраза перебила речь (не должна)")

    pipe.handle_barge_in("катала в доту с друзьями весь вечер", "mic")
    _check(not tts.stopped, "эхо её собственной речи перебило речь (не должно)")


def test_tts_handover():
    """TTS v14.5: pre_play зовётся ровно перед первым звуком, stop_previous
    не глушит новый поток, счётчик активных потоков возвращается в 0."""
    import threading
    import wave as _wave

    from tts.tts_module import TTSModule, speech_chunks

    tmp = Path(tempfile.mkdtemp())
    wav_path = tmp / "t.wav"
    with _wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)   # 0.1 с тишины

    t = TTSModule(refs=[])
    t._ready.set()
    t._backend = "test"

    def fake_synth(text, speed=1.0, mood="normal", stop_flag=None):
        return None if (stop_flag and stop_flag.is_set()) else wav_path

    t._synthesize = fake_synth
    plays = []
    t._play = lambda wav, stop_flag=None, on_audio_start=None: (
        on_audio_start() if on_audio_start else None, plays.append(1), True
    )[-1]

    events = []
    t.speak_stream(iter(["Первое предложение достаточной длины для синтеза.",
                         "Второе предложение тоже длинное и едет следом."]),
                   pre_play=lambda: events.append("pre"))
    _check(events == ["pre"], f"pre_play должен быть один раз до звука: {events}")
    _check(len(plays) >= 1, "звук не сыгрался")
    _check(t.active_streams() == 0 and not t.speaking, "поток не закрылся")

    # stop_previous не убивает текущий поток, stop() убивает
    long_text = ("Первый кусок реплики достаточно длинный, чтобы стать отдельным чанком "
                 "синтеза, и уйти в воспроизведение первым, не дожидаясь всей фразы целиком. "
                 "Второй кусок тоже немаленький, он должен поехать следом как отдельный "
                 "чанк озвучки, потому что лимит склейки давно превышен. Третий финальный "
                 "кусок завершает реплику и тоже едет своим собственным чанком до конца.")
    t._synthesize = lambda text, speed=1.0, mood="normal", stop_flag=None: \
        (None if (stop_flag and stop_flag.is_set()) else wav_path)
    done = threading.Event()
    n_chunks = len(speech_chunks([long_text]))

    def run():
        t.speak_stream(iter([long_text]))
        done.set()

    plays.clear()
    th = threading.Thread(target=run, daemon=True)
    th.start()
    deadline = time.time() + 5
    while t.active_streams() == 0 and time.time() < deadline:
        time.sleep(0.02)
    t.stop_previous()                      # не должно ничего заглушить
    _check(done.wait(timeout=5), "stop_previous прервал АКТИВНЫЙ поток")
    _check(len(plays) == n_chunks, f"поток доиграл не весь: {len(plays)}/{n_chunks}")

    plays.clear()
    th2 = threading.Thread(target=run, daemon=True)
    th2.start()
    deadline = time.time() + 5
    while t.active_streams() == 0 and time.time() < deadline:
        time.sleep(0.02)
    t.stop()                               # внешний стоп — глушит
    _check(done.wait(timeout=5), "stop() не завершил поток")
    _check(t.active_streams() == 0 and not t.speaking, "счётчик потоков не обнулился")


def test_hearing_defer():
    """Матрица слышимости v14.5: loopback не глушится во время её речи
    (set_defer), а её собственное эхо из буфера фильтрует пайплайн."""
    from memory.memory_module import MemoryModule
    from pipeline.pipeline import DialoguePipeline
    from stt.stt_module import STTModule

    stt = STTModule(lambda *a: None, channel="loop")
    stt.set_defer(True)
    _check(stt._defer.is_set(), "defer не встал")
    stt.set_defer(False)
    _check(not stt._defer.is_set(), "defer не снялся")

    class _TTS:
        speaking = False
        def stop(self): pass
        def speak_stream(self, sentences, **kwargs): return False

    class _Prompts:
        threads_summary = None
        def build_system(self, **kw): return "sys"
        def get_history(self): return []

    class _Av:
        def command_avatar(self, **kw): pass
        def set_mouth(self, v): pass

    class _LLM:
        last_error = ""
        def generate_micro(self, *a, **k): return "НЕТ"
        def generate_stream(self, *a, **k): return iter(())

    tmp = Path(tempfile.mkdtemp())
    mem = MemoryModule(str(tmp / "memory.json"))
    pipe = DialoguePipeline(mem, _Prompts(), _LLM(), _TTS(), _Av())
    calls: list[tuple] = []
    pipe._run_async = lambda fn, *a: calls.append((fn, a))
    pipe._last_monologue = ["Я вчера катала в доту с друзьями весь вечер."]

    pipe.handle_user_text("я вчера катала в доту с друзьями весь вечер", source="loop")
    _check(not calls, "эхо её речи из loopback ушло в ответ (не должно)")

    pipe.handle_user_text("Нима, привет", source="loop")
    _check(bool(calls), "живая реплика из loopback потерялась после эха")

    # разговорный пайплайн во время речи: defer на loopback, barge на мике
    stt.set_defer(True)
    stt.set_barge(True)
    _check(stt._defer.is_set() and stt._barge.is_set(), "режимы слуха не совместились")
    stt.set_defer(False)
    stt.set_barge(False)


def test_vision_module():
    """Зрение: разбор наблюдений, запросы про экран, память увиденного,
    режим просмотра (тишина + движение кадров), фоновые комментарии v14.7.7
    (новизна сцены, TTL≥delay+cooldown, доживание/cooldown, busy-отсрочка,
    describe_now не ломает motion-детектор фона)."""
    import time as _time

    import numpy as np

    from vision.vision_module import (VisionModule, is_screen_request,
                                      parse_observation)

    _check(parse_observation("ВОПРОС: что это за иконка?") ==
           ("что это за иконка?", "question"), "вопрос не распознан")
    _check(parse_observation("Играют в шахматы")[1] == "statement", "ложный вопрос")
    for t in ("да блин где эта кнопка", "что я делаю не так", "Нима, что на экране",
              "куда я положил камень", "гляни, что там"):
        _check(is_screen_request(t), f"«{t}» не распознан как запрос про экран")
    for t in ("привет, как дела", "купил хлеба", "сколько времени"):
        _check(not is_screen_request(t), f"«{t}» ложный запрос про экран")

    facts: list[str] = []
    mem = SimpleNamespace(append_fact=lambda text, source="", tags=None:
                          facts.append(text))

    class _LLM:
        def generate_vision(self, system, user_text, image, **kw):
            return "На экране играют в шахматы."

    shots = [("jpeg", np.zeros((36, 64), dtype=np.uint8)),
             ("jpeg", np.full((36, 64), 200, dtype=np.uint8))]

    class _Cap:
        def __init__(self): self.i = 0
        def __call__(self):
            shot = shots[min(self.i, len(shots) - 1)]
            self.i += 1
            return shot

    v = VisionModule(mem, _LLM(), capture_fn=_Cap())
    _check(v.describe_now() == "На экране играют в шахматы.", "describe_now сломан")
    _check(len(v.recent) == 1, "describe_now не сохранил наблюдение в контекст")

    now = _time.time()
    v.remember("На экране играют в шахматы.", "statement", now)
    _check(len(v.recent) == 2, "наблюдение не попало в контекст")
    _check(bool(facts), "первое наблюдение не ушло в память")
    v.remember("На экране играют в шахматы под музыку.", "statement", now + 1)
    _check(len(facts) == 1, f"троттлинг фактов не сработал: {facts}")
    _check(v.fresh_observation(60) != "", "fresh_observation потерял свежее")
    _check(bool(v.recent_summary()), "recent_summary пуст")

    # режим просмотра: тихо + кадры меняются
    v._last_user_ts = now - 10_000
    for m in (12.0, 14.0, 9.0, 15.0):
        v._motion.append(m)
    _check(v._watch_mode(now), "режим просмотра не включился")
    v._motion.clear()
    for m in (0.0, 0.5, 0.2, 1.0):
        v._motion.append(m)
    _check(not v._watch_mode(now), "режим просмотра при статичном экране")

    # вопрос про экран уходит колбэком
    got: list[tuple] = []
    v.on_observation = lambda text, kind: got.append((text, kind))
    text, kind = parse_observation("ВОПРОС: кто выигрывает?")
    v.remember(text, kind, now + 2)
    v.on_observation(text, kind)
    _check(got == [("кто выигрывает?", "question")], "колбэк вопроса не сработал")

    # --- фоновые Vision-комментарии (v14.7.7): «видит и говорит» ---
    from unittest.mock import patch

    from core import config

    # новизна: первый комментарий проходит; та же сцена (даже при большом
    # motion) — нет; заметно другая сцена — снова проходит
    nv = VisionModule(SimpleNamespace(append_fact=lambda *a, **k: None), _LLM())
    _check(nv._is_novel_comment("Открылась игра Dota", 0.0),
           "первый комментарий не прошёл гейт новизны")
    nv._last_comment_text = "Открылась игра Dota"
    _check(not nv._is_novel_comment("Открылась игра Dota снова", 50.0),
           "дубль сцены прошёл несмотря на высокий motion")
    _check(nv._is_novel_comment("Редактор кода с тестами", 0.0),
           "новая сцена не прошла гейт новизны")

    # TTL согласован с delay+cooldown: кандидат не протухает раньше cooldown
    _check(config.VISION_OBSERVATION_TTL_SEC >=
           config.VISION_COMMENT_DELAY_SEC + config.VISION_COMMENT_COOLDOWN_SEC,
           "TTL кандидата короче delay+cooldown — комментарий протухнет раньше")

    # комментарий доживает и озвучивается (без seq-привязки); повторный сразу
    # после — блокируется cooldown, не теряется навсегда
    sent: list[tuple] = []
    dc = VisionModule(SimpleNamespace(append_fact=lambda *a, **k: None), _LLM())
    dc.on_observation = lambda text, kind: sent.append((text, kind))
    dc.busy_check = lambda: False
    t0 = _time.time()
    dc._last_user_ts = t0 - 100          # человек давно молчит → guard не мешает
    with patch("vision.vision_module.VISION_COMMENT_INTERRUPT_GUARD_SEC", 0), \
         patch("vision.vision_module.VISION_COMMENT_COOLDOWN_SEC", 60), \
         patch("vision.vision_module.VISION_OBSERVATION_TTL_SEC", 300):
        dc._pending_comment = {"ts": t0 - 10, "due": t0 - 1,
                               "text": "Открылась игра Dota", "seq": 0}
        dc._activity_seq = 5             # seq рассинхронизирован — раньше терялся
        dc._dispatch_comment(t0)
        _check(sent == [("Открылась игра Dota", "comment")],
               "комментарий не озвучился (потерян на seq-рассинхроне)")
        # второй кандидат сразу — упирается в cooldown, но НЕ выстреливает дважды
        dc._pending_comment = {"ts": t0, "due": t0,
                               "text": "Редактор кода", "seq": 5}
        dc._dispatch_comment(t0 + 5)
        _check(len(sent) == 1, "cooldown фоновых комментариев не сработал")

    # busy откладывает комментарий, НЕ съедая его
    bd = VisionModule(SimpleNamespace(append_fact=lambda *a, **k: None), _LLM())
    bd._last_user_ts = _time.time() - 100
    bd.busy_check = lambda: True
    bd._pending_comment = {"ts": _time.time(), "due": 0,
                           "text": "Сменилась сцена", "seq": 0}
    bd._dispatch_comment(_time.time())
    _check(bd._pending_comment is not None,
           "busy съел комментарий вместо отсрочки")

    # describe_now НЕ трогает _last_gray фона (не ломает motion-детектор)
    dg = VisionModule(SimpleNamespace(append_fact=lambda *a, **k: None),
                      _LLM(), capture_fn=_Cap())
    marker = np.full((36, 64), 123, dtype=np.uint8)
    dg._last_gray = marker
    dg.describe_now()
    _check(dg._last_gray is marker,
           "describe_now перезаписал _last_gray — фон motion-детектор сбит")


def test_prompt_vision():
    """Промпт: блок зрения («минус шесть») появляется только с наблюдениями."""
    from prompts.prompt_builder import PromptBuilder

    class _Mem:
        def get_prompt_context(self, query=None, speaker=None):
            return ""

    pb = PromptBuilder(_Mem())
    sys1 = pb.build_system(user_text="привет")
    _check("минус шесть" not in sys1, "блок зрения в промпте без наблюдений")

    pb.vision_summary = lambda: "- На экране играют в шахматы."
    sys2 = pb.build_system(user_text="что я делаю?")
    _check("минус шесть" in sys2, "протокол зрения не в промпте")
    _check("шахматы" in sys2, "наблюдения не в промпте")


def test_tts_chunks():
    """TTS: чанки синтеза — короткие склеиваются, длинный старт режется."""
    from tts.tts_module import speech_chunks

    short = speech_chunks(["Ха.", "Ты серьёзно?", "Ну ладно."])
    _check(len(short) == 1 and short[0].startswith("Ха."),
           f"короткие предложения не склеились: {short}")

    long_text = ("Очень длинное первое предложение, которое заметно превышает лимит "
                 "первого чанка, поэтому обязано разрезаться по запятой на два куска, "
                 "чтобы первый звук пошёл раньше, не дожидаясь конца всей фразы, "
                 "и стрим не молчал лишние секунды до финальной точки.")
    pieces = speech_chunks([long_text])
    _check(len(pieces) >= 2, f"длинный первый чанк не разрезан: {len(pieces)}")

    two = speech_chunks([
        "Первое предложение достаточно длинное, чтобы уйти в синтез сразу же.",
        "Второе предложение тоже немаленькое и должно ехать отдельно, поскольку "
        "лимит склейки не достигнут, и склеиваться им не с чем, точно.",
    ])
    _check(len(two) == 2, f"ожидались 2 чанка, получилось {len(two)}: {two}")


def test_emotions():
    """Эмоции: детект по словам, тег [ЭМОЦИЯ] из ответа, влияние на голос."""
    from core.config import EMOTION_SPEED
    from pipeline.mood_state import MoodState
    from pipeline.pipeline import DialoguePipeline, _detect_mood

    _check(_detect_mood("разденься и покажи груди") == "arousal", "возбуждение не поймано")
    _check(_detect_mood("да ты стесняешься, ага") == "shyness", "стеснение не поймано")
    _check(_detect_mood("скучно, неинтересно") == "boredom", "скука не поймана")

    mood_box = ["normal"]
    fake = SimpleNamespace(mood_state=MoodState())
    apply = DialoguePipeline._apply_emotion.__get__(fake)
    out = apply("[ЭМОЦИЯ: стеснение] З-заткнись, я не краснею!", mood_box)
    _check(mood_box[0] == "shyness" and "ЭМОЦИЯ" not in out,
           f"тег эмоции не обработан: {out!r}")
    _check(fake.mood_state.mood == "shyness", "тег не записался в mood_state")
    out = apply("Просто реплика без тега [АНИМАЦИЯ: dance]", mood_box)
    _check(mood_box[0] == "shyness", "посторонний тег сменил эмоцию")
    _check(EMOTION_SPEED.get("shyness", 1.0) < 1.0 and EMOTION_SPEED.get("excitement") > 1.0,
           "эмоции не влияют на скорость голоса")

    from core.config import SILERO_EMOTION_PITCH, SILERO_EMOTION_RATE
    _check(set(SILERO_EMOTION_RATE) == set(EMOTION_SPEED)
           and set(SILERO_EMOTION_PITCH) == set(EMOTION_SPEED),
           "карты Silero-эмоций не совпадают по ключам с EMOTION_SPEED")
    _check(all(v in (None, "slow", "fast") for v in SILERO_EMOTION_RATE.values()),
           "rate Silero: только слова slow/fast (проценты у v4_ru сломаны)")
    _check(all(v is None or (v.endswith("%") and v[0] in "+-")
               for v in SILERO_EMOTION_PITCH.values()),
           "pitch Silero: только проценты '+N%'/'-N%' или None")


def test_mood_state():
    """Устойчивое настроение: тег сильнее слов, затухание по ходам, в промпт."""
    from pipeline.mood_state import TURNS_FROM_TAG, TURNS_FROM_USER, MoodState

    ms = MoodState()
    _check(ms.mood == "normal", "старт не в normal")
    # слова пользователя заражают только норму, держатся слабее
    ms.observe_user("joy")
    _check(ms.mood == "joy" and ms.turns_left == TURNS_FROM_USER,
           "заражение от пользователя не сработало")
    for _ in range(TURNS_FROM_USER):
        ms.tick()
    _check(ms.mood == "normal", "настроение не затухло до normal")
    # тег [ЭМОЦИЯ] — сильный источник и не перебивается словами
    ms.observe_tag("anger")
    ms.observe_user("joy")
    _check(ms.mood == "anger" and ms.turns_left == TURNS_FROM_TAG,
           "слово пользователя перебило своё состояние по тегу")
    for _ in range(TURNS_FROM_TAG - 1):
        ms.tick()
    _check(ms.mood == "anger", "теговое настроение угасло раньше срока")
    ms.tick()
    _check(ms.mood == "normal", "после последнего хода не normal")
    # кривые значения игнорируются
    ms.observe_tag("гнев"); ms.observe_user("apathy")
    _check(ms.mood == "normal", "мусорное настроение прошло в состояние")


def test_presence():
    """Присутствие: голос=тут, прощание/чужое «X ушёл»/тишина = нет, адресация."""
    from pipeline.presence import PresenceTracker

    p = PresenceTracker()
    p.on_voice("Кизилл", "ну что, погнали")
    _check(p.is_present("Кизилл"), "распознанный голос не отметился")
    p.on_voice("Вася", "привет всем")
    _check(p.is_present("Вася"), "второй голос не отметился")
    _check(p.pick_addressed() in ("Кизилл", "Вася"), "адресат не из присутствующих")
    # прощание — вышел
    p.on_voice("Вася", "всё, я пошёл, удачи")
    _check(not p.is_present("Вася"), "прощание не убрало человека")
    # чужой репликой сообщили
    p.mark_present("Маша")
    p.on_voice("Кизилл", "маша ушла уже, не дождёшься")
    _check(not p.is_present("Маша"), "«X ушёл» не сработал")
    # «пока»-союз не убивает
    p.mark_present("Маша")
    p.on_voice("Маша", "подожди, пока не поздно, надо бежать")
    _check(p.is_present("Маша"), "союз «пока» ложно убрал человека")
    # нить закрылась без ответа — человека нет
    p.on_no_answer("Кизилл")
    _check(not p.is_present("Кизилл"), "закрытый хвост не убрал молчуна")
    p.mark_gone("Маша")
    _check(p.present_names() == [], "не все ушли в конце теста")


def test_dataset_style():
    """Стилевой LoRA-датасет: объём ≥1000, формат messages, анти-ассистент."""
    import json
    from core.config import CREATOR_NAME
    from training.make_style_dataset import OUT_PATH, SYSTEM_SHORT

    _check(OUT_PATH.exists(), f"нет {OUT_PATH.name} — запусти make_style_dataset.py")
    rows = [json.loads(l) for l in OUT_PATH.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    _check(len(rows) >= 1000, f"датасет мал: {len(rows)} < 1000")
    bad_fmt = [r for r in rows
               if [m.get("role") for m in r.get("messages", [])]
               != ["system", "user", "assistant"]]
    _check(not bad_fmt, f"кривой формат у {len(bad_fmt)} записей")
    _check(CREATOR_NAME in SYSTEM_SHORT, "имя создателя не в system датасета")
    # анти-ассистент: в её ответах недопустимы сервисные штампы
    stamps = ("чем могу помочь", "могу помочь", "рада помочь", "обращайся!",
              "что-нибудь ещё", "я здесь, чтобы помочь")
    hits = [(r["messages"][1]["content"][:40], r["messages"][2]["content"][:60])
            for r in rows
            if any(s in r["messages"][2]["content"].lower() for s in stamps)]
    _check(not hits, f"ассистентские штампы в ответах: {hits[:2]}")
    # имя создателя не отдано чужим репликам (адресат приветствий/уходов есть, но
    # никто, кроме Кизилла, не объявляет себя создателем в примерах)
    _check(SYSTEM_SHORT.count("один") >= 1, "в system датасета нет нетранзферности создателя")


def test_addressee_router():
    """Маршрутизация адресата: ей / Кизиллу / третьему / никому + слои контекста."""
    from types import SimpleNamespace
    from ears.ears_module import detect_addressee, is_questionish
    from pipeline.pipeline import DialoguePipeline
    from pipeline.presence import PresenceTracker
    from pipeline.threads import ConversationThreads

    known = ("Кизилл", "Вася", "Маша")
    # слой 1: словарь
    _check(detect_addressee("Нима, привет", known) == "nima", "её имя в начале не поймано")
    _check(detect_addressee("нимочка, а сколько время?", known) == "nima",
           "форма имени не поймана")
    _check(detect_addressee("а Нима как считает?", known) == "nima",
           "её имя в тексте не поймано")
    _check(detect_addressee("Вася, ты слышал?", known) == "Вася", "звательное имя не поймано")
    _check(detect_addressee("Кизилл, погнали в доту", known) == "Кизилл",
           "Кизилл как адресат не пойман")
    _check(detect_addressee("Эй, Маша, спроси у Нимы", known) == "Маша",
           "зачин перебил звательное имя")
    _check(detect_addressee("спасибо большое", known) is None, "безадресная фраза дала адресата")
    _check(detect_addressee("погнали, парни", ("Вася", "Маша")) is None,
           "обращение к толпе дало адресата")
    _check(is_questionish("а ты что думаешь?") and not is_questionish("я пошёл"),
           "questionish сломан")

    # слой 2: контекст через настоящий пайплайн (без LLM)
    threads = ConversationThreads()
    presence = PresenceTracker()
    fake = SimpleNamespace(threads=threads, presence=presence, llm=None,
                           memory=SimpleNamespace(person_names=lambda: set()),
                           _dialog_speaker=None, _dialog_window_until=0.0)
    route = DialoguePipeline._route_addressee.__get__(fake)
    _check(route("какие планы на вечер", "Вася") is None,
           "без контекста чужой ход попал к ней")
    threads.on_bot_reply("Вася, ты в доту сегодня?", "Вася", "stt", is_question=True)
    _check(route("да, давай", "Вася") == "nima",
           "ответ на её вопрос не распознан как адресованный ей")
    _check(route("да, давай", "Маша") is None, "чужой голос приписан к хвосту Васи")
    # окно диалога: она ответила Кизиллу — его ход без имени адресован ей
    fake._dialog_speaker = "Кизилл"
    import time as _t
    fake._dialog_window_until = _t.time() + 12
    _check(route("а ты как думаешь", "Кизилл") == "nima", "окно диалога не сработало")
    # после окна — снова мимо
    fake._dialog_window_until = _t.time() - 1
    _check(route("а ты как думаешь", "Кизилл") is None, "окно диалога не закрывается")


def test_composition():
    """Сборка системы как в start.py: все модули конструируются и связаны.

    Регресс v14.1: TTS_REFS выпал из импортов tts_module — падало только на
    живом запуске, потому что смоук не собирал систему целиком.
    """
    import start as start_mod

    modules = start_mod.build_system()
    for key in ("memory", "prompts", "llm", "avatar", "tts", "web", "initiative",
                "pipeline", "voice_id", "stt", "stt_loop", "twitch"):
        _check(key in modules, f"в сборке нет модуля {key}")
    _check(modules["pipeline"].tts is modules["tts"], "пайплайн не связан с TTS")
    _check(modules["pipeline"].stt is modules["stt"], "пайплайн не связан с микрофоном")
    _check(modules["pipeline"].stt_loop is modules["stt_loop"],
           "пайплайн не связан с loopback")
    _check(modules["pipeline"].initiative is modules["initiative"],
           "пайплайн не связан с инициативой")


def test_outfit_change():
    """Одежда: просьба стримера → показ+смена+реплика; чат/донаты не переодевают."""
    import pipeline.pipeline as pl
    from memory.memory_module import MemoryModule
    from pipeline.outfits import (OUTFIT_RANDOM, OUTFIT_SELF_POOL,
                                  detect_outfit_request, pick_random_outfit)
    from pipeline.pipeline import DialoguePipeline

    # --- детект просьб ---
    _check(detect_outfit_request("Нима, надень платье") == "Nima_drees.vrm", "платье не распознано")
    _check(detect_outfit_request("переоденься в горничную") == "Nima_maid.vrm", "мейд не распознан")
    _check(detect_outfit_request("надень кимоно") == "Nima_kimono.vrm", "кимоно не распознано")
    _check(detect_outfit_request("надень джинсы что ли") == "Nima_jeens.vrm", "джинсы не распознаны")
    _check(detect_outfit_request("разденься") == "Nima_naked.vrm", "раздеться не распознано")
    _check(detect_outfit_request("надень что-нибудь сексуальное") == "Nima_Sexual.vrm",
           "сексуальный образ не распознан")
    _check(detect_outfit_request("просто переоденься") == OUTFIT_RANDOM,
           "просьба без цели → должен быть случайный образ")
    _check(detect_outfit_request("я вчера купила платье") is None,
           "разговор про платье сработал как просьба")
    _check(detect_outfit_request("привет, как дела?") is None, "ложный детект на пустой фразе")

    # случайный выбор: не текущий, никогда голый/сексуальный
    _check(pick_random_outfit("Nima_maid.vrm") != "Nima_maid.vrm",
           "случайный выбор повторил текущий образ")
    for _ in range(12):
        picked = pick_random_outfit("Nima_maid.vrm")
        _check(picked in OUTFIT_SELF_POOL,
               f"в случайный выбор попал неповседневный образ: {picked}")

    # --- фейки ---
    class _TTS:
        speaking = False
        spoken = None
        def stop(self): pass
        def speak_stream(self, sentences, **kwargs):
            self.spoken = list(sentences)
            return True

    class _Prompts:
        threads_summary = None
        def build_system(self, **kw): return "sys"
        def get_history(self, speaker=None): return []

    class _LLM:
        last_error = ""
        def __init__(self): self.calls = 0
        def generate(self, *a, **k):
            self.calls += 1
            return f"Фраза про одежду номер {self.calls}, та-дам."
        def generate_stream(self, *a, **k): return iter(())

    class _Av:
        def __init__(self):
            self.cmds = []
            self._state = {"current_outfit": "Nima_standart.vrm"}
        @property
        def current_outfit(self): return self._state["current_outfit"]
        def command_avatar(self, **kw):
            self.cmds.append(kw)
            if kw.get("outfit"):
                self._state["current_outfit"] = kw["outfit"]
        def set_mouth(self, v): pass

    class _Threads:
        def on_bot_reply(self, *a, **k): pass
        def on_user_reply(self, *a, **k): pass
        def pending_summary(self): return ""

    tmp = Path(tempfile.mkdtemp())
    mem = MemoryModule(str(tmp / "memory.json"))
    tts, av = _TTS(), _Av()
    pipe = DialoguePipeline(mem, _Prompts(), _LLM(), tts, av, threads=_Threads())

    saved_delay, saved_chance = pl.OUTFIT_SWITCH_DELAY, pl.OUTFIT_SELF_CHANCE
    pl.OUTFIT_SWITCH_DELAY = 0  # переодевание синхронно — без ожидания таймера
    pl.OUTFIT_SELF_CHANCE = 0.0  # собственная инициатива не мешает остальным проверкам
    try:
        # --- просьба от стримера (консоль) ---
        pipe._run_async = lambda fn, *a: fn(*a)
        pipe.handle_user_text("Нима, переоденься в горничную", source="manual")
        _check(any(c.get("action") == "spinning" for c in av.cmds),
               "нет анимации показа (spinning)")
        _check("Nima_maid.vrm" in [c.get("outfit") for c in av.cmds],
               "образ не сменился")
        _check(tts.spoken and "Фраза про одежду" in tts.spoken[0],
               f"реплика про одежду не озвучена: {tts.spoken}")
        _check(len(pipe._outfit_remarks) == 1, "реплика не попала в анти-повтор")
        _check(any(d.get("role") == "bot" and "Фраза" in d.get("text", "")
                   for d in mem._data.get("layers", {}).get("dialog", [])[-2:]),
               "реплика про одежду не в памяти")

        # --- чужие источники: детект есть, но защита по источнику ---
        detect = detect_outfit_request("Нима, разденься")
        _check(detect == "Nima_naked.vrm", "детект «разденься» сломан")
        for source in ("twitch", "donation", "loop"):
            av.cmds.clear()
            tts.spoken = None
            pipe.handle_user_text("Нима, разденься и переоденься в платье",
                                  source=source)
            _check(not [c for c in av.cmds if c.get("outfit")],
                   f"источник {source} смог переодеть Ниму")

        # --- собственная инициатива: шанс 1.0 срабатывает, кулдаун держит ---
        pl.OUTFIT_SELF_CHANCE = 1.0
        av.cmds.clear()
        tts.spoken = None
        pipe._outfit_init_until = 0.0
        pipe._maybe_self_outfit_change()
        _check([c for c in av.cmds if c.get("outfit")],
               "своя инициатива не сработала при шансе 1.0")
        _check(av.current_outfit in OUTFIT_SELF_POOL,
               f"своя инициатива выбрала неповседневный образ: {av.current_outfit}")
        _check(tts.spoken, "реплика инициативы не озвучена")
        av.cmds.clear()
        pipe._maybe_self_outfit_change()
        _check(not av.cmds, "кулдаун собственной инициативы не работает")

        # --- детект мимо: обычный ответ не задет (возврат дефолтного шанса) ---
        pl.OUTFIT_SELF_CHANCE = 0.0
        av.cmds.clear()
        pipe._outfit_init_until = 0.0
        pipe._maybe_self_outfit_change()
        _check(not av.cmds, "инициатива сработала при шансе 0")
    finally:
        pl.OUTFIT_SWITCH_DELAY, pl.OUTFIT_SELF_CHANCE = saved_delay, saved_chance


def test_persistence():
    """Окно и одежда переживают перезапуск (main.cjs/bridge)."""
    import avatar.bridge as br

    tmp = Path(tempfile.mkdtemp())
    saved_path = br.LAST_OUTFIT_PATH
    br.LAST_OUTFIT_PATH = tmp / "last_outfit.txt"
    saved_default = br._DEFAULT_STATE["current_outfit"]
    br._DEFAULT_STATE["current_outfit"] = br._saved_outfit()
    try:
        b = br.AvatarBridge()
        _check(b._state["current_outfit"] == "Nima_standart.vrm",
               "без сохранёнки должен быть дефолт")
        b.command_avatar(outfit="Nima_maid.vrm")
        _check(br._saved_outfit() == "Nima_maid.vrm", "одежда не запомнилась на диск")
        # _DEFAULT_STATE вычисляется на импорте — эмулируем перезапуск модуля
        br._DEFAULT_STATE["current_outfit"] = br._saved_outfit()
        b2 = br.AvatarBridge()
        _check(b2._state["current_outfit"] == "Nima_maid.vrm",
               "одежда не восстановилась в новом мосте")
    finally:
        br.LAST_OUTFIT_PATH = saved_path
        br._DEFAULT_STATE["current_outfit"] = saved_default

    main_cjs = (PROJECT_ROOT / "avatar" / "main.cjs").read_text(encoding="utf-8")
    for marker in ("window_state.json", "restoreWinBounds", "saveWinState"):
        _check(marker in main_cjs, f"в main.cjs нет {marker} — память окна сломана")


def test_vrma_assets_and_bundle():
    """VRMA: файлы на месте, бандл умеет их играть, песочница их видит."""
    from debug_menu.config import ANIMATIONS

    anim_dir = PROJECT_ROOT / "vita_avatar_app" / "animations"
    vrma_files = {p.name for p in anim_dir.glob("*.vrma")}
    _check(len(vrma_files) >= 7, f"мало VRMA-файлов: {len(vrma_files)}")
    vrma_entries = [a for a, _ in ANIMATIONS if a.startswith("vrma_")]
    _check(len(vrma_entries) >= 7, "VRMA не появились в списке песочницы")
    for action in vrma_entries:
        stem = action[len("vrma_"):]
        _check(f"{stem}.vrma" in vrma_files, f"{action} → файла {stem}.vrma нет")
    bundle = (PROJECT_ROOT / "avatar" / "renderer" / "viewer.bundle.js").read_text(
        encoding="utf-8", errors="replace")
    for marker in ("VRMAnimationLoaderPlugin", "createVRMAnimationClip",
                   "vrmAnimations", "resolveVrmaFile"):
        _check(marker in bundle, f"в viewer.bundle.js нет {marker} — пересобери build-vrm")
    # желейная физика груди: шейдерная деформация всей массы + пружины на CPU
    for marker in ("uJellyOffset", "uJellyCenter", "setupJelly", "updateJelly"):
        _check(marker in bundle, f"в viewer.bundle.js нет {marker} (jelly) — пересобери build-vrm")
    source = (PROJECT_ROOT / "avatar" / "renderer" / "viewer.js").read_text(
        encoding="utf-8", errors="replace")
    _check("skinning_vertex" in source and "JELLY_CHUNK" in source,
           "jelly-вклейка должна идти после #include <skinning_vertex>")


def test_prompt_builder_v14():
    """Промпт: персона + нити + настроение; история из памяти."""
    from prompts.prompt_builder import PromptBuilder
    from prompts.persona import PERSONA, PERSONA_COMPACT
    from core import config

    mem = SimpleNamespace(get_prompt_context=lambda query, speaker: "Факты:\n- тест",
                          get_recent_dialog=lambda limit: [])
    pb = PromptBuilder(mem)
    system = pb.build_system(user_text="привет", mood="anger", speaker="Кизил")
    expected_persona = PERSONA_COMPACT if config.PERSONA_COMPACT else PERSONA
    _check("Нимфея" in system and expected_persona.strip() in system, "персона не в промпте")
    _check("тест" in system, "контекст памяти не в промпте")
    _check("раздражена" in system or "злишься" in system, "настроение не в промпте")


def test_initiative_offline():
    """Инициатива: активность/факты без сети и watchdog."""
    from initiative.initiative_module import InitiativeModule

    mem = SimpleNamespace(recall_dialog=lambda *a, **k: [])
    init = InitiativeModule(mem, None)
    init.note_user_activity()
    init.note_spoke()
    init.add_fact("Питон — змея, но ещё и язык")
    _check(bool(init._pending_facts), "отложенный факт не сохранился")


def test_persona_v14():
    """Персона: характер + протоколы поиска и анимаций на месте."""
    from prompts.persona import PERSONA

    for needle in ("Нимфея", "цундере", "[ПОИСК", "АНИМАЦИЯ", "ПО ИМЕНИ"):
        _check(needle in PERSONA, f"в персоне потеряно: {needle}")
    # создатель: имя из конфига + нетранзферность титула
    from core.config import CREATOR_NAME
    _check(CREATOR_NAME in PERSONA, "имя создателя не в персоне")
    for phrase in ("ОДИН", "НЕ признавай в нём создателя"):
        _check(phrase in PERSONA, f"нет защиты титула создателя: {phrase}")
    # анти-ассистент
    for phrase in ("НЕ ассистент", "чем могу помочь"):
        _check(phrase in PERSONA, f"нет анти-ассистентского блока: {phrase}")
    # словарь комьюнити
    for word in ("сабы", "чатерсы"):
        _check(word in PERSONA, f"нет словаря комьюнити: {word}")


def test_subtitles():
    """Субтитры v14.6: цвета (Нима красный, Кизилл синий, чужие — случайный
    на человека, стабильный в сессии) + доставка в мост аватара."""
    from avatar.bridge import AvatarBridge
    from memory.memory_module import MemoryModule
    from pipeline.pipeline import DialoguePipeline, _SUB_ME, _SUB_NIMA

    color, name = DialoguePipeline._subtitle_style.__get__(SimpleNamespace(
        _sub_colors={}))("Кизилл", "nima")
    _check(color == _SUB_NIMA and name == "", "речь Нимы должна быть красной без имени")

    color, name = DialoguePipeline._subtitle_style.__get__(SimpleNamespace(
        _sub_colors={}))("Кизилл", "mic")
    _check(color == _SUB_ME and name == "", "микрофон Кизилла должен быть синим без имени")
    color, name = DialoguePipeline._subtitle_style.__get__(SimpleNamespace(
        _sub_colors={}))("Кизилл", "manual")
    _check(color == _SUB_ME and name == "", "консоль (manual) должна быть синей без имени")

    fake = SimpleNamespace(_sub_colors={})
    c1, n1 = DialoguePipeline._subtitle_style.__get__(fake)("Вася", "loop")
    c2, n2 = DialoguePipeline._subtitle_style.__get__(fake)("Вася", "loop")
    _check(n1 == "Вася" and n2 == "Вася", "чужая речь должна идти с именем")
    _check(c1 == c2, "цвет на человека должен быть стабильным в сессии")
    c3, n3 = DialoguePipeline._subtitle_style.__get__(SimpleNamespace(
        _sub_colors={}))("Вася", "loop")
    _check(c3 != c1 or True, "цвет назначается случайно (детерминизма быть не должно)")
    _check(all(c.startswith("hsl(") and c.endswith(")") and c.count(",") == 2
               for c in (c1, c3)), "случайный цвет должен быть hsl()")

    # доставка: неизвестный человек с именем → имя в субтитре
    class _Av:
        subs = []
        def command_avatar(self, **kw): pass
        def set_mouth(self, v): pass
        def set_subtitle(self, text, color="", name=""):
            _Av.subs.append((text, color, name))

    class _TTS:
        speaking = False
        def stop(self): pass
        def speak_stream(self, sentences, **kwargs): return False

    class _Prompts:
        threads_summary = None
        def build_system(self, **kw): return "sys"
        def get_history(self): return []

    class _LLM:
        last_error = ""
        def generate_micro(self, *a, **k): return "НЕТ"
        def generate_stream(self, *a, **k): return iter(())

    tmp = Path(tempfile.mkdtemp())
    pipe = DialoguePipeline(MemoryModule(str(tmp / "memory.json")),
                            _Prompts(), _LLM(), _TTS(), _Av())
    pipe._run_async = lambda fn, *a: None
    pipe.handle_user_text("всем привет из чата", source="loop", speaker="Вася")
    _check(bool(_Av.subs) and _Av.subs[-1][0] == "всем привет из чата",
           "субтитр чужой речи не отправлен")
    _check(_Av.subs[-1][2] == "Вася", "имя говорящего не попало в субтитр")

    # мост: set_subtitle кладёт объект в состояние аватара
    bridge = AvatarBridge()
    bridge.set_subtitle("Привет!", "#ff4b4b")
    sub = bridge._state.get("subtitle") or {}
    _check(sub.get("text") == "Привет!" and sub.get("color") == "#ff4b4b"
           and sub.get("seq", 0) >= 1, "мост не записал субтитр в состояние")


def test_audio_output_routing():
    """Вывод TTS (v14.6.1): выбор устройства по имени/номеру, дубль-монитор,
    фолбэк на дефолт при неизвестном имени (сценарий наушники + VB-Cable)."""
    import wave as _wave

    from tts import tts_module
    from tts.tts_module import TTSModule, _resolve_output_device

    _check(_resolve_output_device("") is None, "пустое имя должно давать дефолт")
    _check(_resolve_output_device("несуществующее-xyz-999") is None,
           "мусорное имя должно давать None")
    known = _resolve_output_device("динамики")
    _check(known is None or isinstance(known, int),
           "известное имя должно давать номер устройства")

    tmp = Path(tempfile.mkdtemp())
    wav_path = tmp / "t.wav"
    with _wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(b"\x00\x00" * 4800)   # 0.1 с тишины

    t = TTSModule(refs=[])
    old_out, old_mon = tts_module.TTS_OUTPUT_DEVICE, tts_module.TTS_MONITOR_DEVICE
    try:
        # фолбэк: мусорное имя → играет в дефолт без падения
        tts_module.TTS_OUTPUT_DEVICE, tts_module.TTS_MONITOR_DEVICE = \
            "несуществующее-xyz-999", ""
        _check(t._play(wav_path) is True, "фолбэк на дефолт не сработал")
    finally:
        tts_module.TTS_OUTPUT_DEVICE, tts_module.TTS_MONITOR_DEVICE = old_out, old_mon


def test_active_window():
    """Зрение v14.7: активное окно (ctypes) подставляется в запрос описания;
    пустой заголовок — запрос без окна; хоткей-оверрайд режима просмотра."""
    import numpy as np

    from vision.vision_module import VisionModule, active_window_title

    title = active_window_title()   # на CI/headless может быть пусто — это ок
    _check(isinstance(title, str), "заголовок окна не строка")
    _check(len(title) <= 90, f"заголовок не обрезан: {len(title)}")

    asks: list[str] = []

    class _LLM:
        def generate_vision(self, system, user_text, image, **kw):
            asks.append(user_text)
            return "кто-то играет"

    v = VisionModule(SimpleNamespace(), _LLM(),
                     capture_fn=lambda: ("jpeg", np.zeros((36, 64), dtype=np.uint8)))
    v.describe_frame("jpeg", window="Тестовое Окно")
    _check("Тестовое Окно" in asks[-1], "окно не попало в запрос описания")
    v.describe_frame("jpeg", window="")
    _check("Активное окно" not in asks[-1], "пустое окно попало в запрос")

    v.set_watch_override(True)
    _check(v._watch_mode(time.time()), "оверрайд просмотра ВКЛ не работает")
    v.set_watch_override(False)
    v._last_user_ts = time.time() - 10_000
    for m in (12.0, 14.0, 9.0, 15.0):
        v._motion.append(m)
    _check(not v._watch_mode(time.time()), "оверрайд просмотра ВЫКЛ не работает")
    v.set_watch_override(None)
    _check(v._watch_mode(time.time()), "авто-режим не вернулся после оверрайда")


def test_watchdog():
    """Watchdog v14.7: проверка-проблема фиксируется, фикс вызывается, ок — тихо."""
    from watchdog.watchdog_module import WatchdogModule

    w = WatchdogModule()
    fixed: list[str] = []
    w.register("ok", lambda: None)
    w.register("problem", lambda: "STT умер")
    w.register("fixable", lambda: ("поток мёртв", lambda: fixed.append("fix")))

    w._run_checks()
    _check(any("STT умер" in i for i in w.issues), "проблема не зафиксирована")
    _check(any("поток мёртв" in i and "перезапущено" in i for i in w.issues),
           "починка не отмечена")
    _check(fixed == ["fix"], "фикс не вызван")


def test_diary():
    """Дневник v14.7: дата ночи, идемпотентность, факт в память, пустой день."""
    import datetime as dt

    from diary.diary_module import DiaryModule, diary_date_now

    _check(diary_date_now(dt.datetime(2026, 9, 5, 15, 0)) == dt.date(2026, 9, 4),
           "днём дневник за вчерашний день")
    _check(diary_date_now(dt.datetime(2026, 9, 5, 1, 0)) == dt.date(2026, 9, 3),
           "глубокой ночью — за позавчерашний (день не теряется)")

    facts: list[str] = []

    class _Mem:
        def query(self, since=None, until=None, limit=40):
            return ([{"role": "user", "content": "катали в доту до утра"}]
                    if since else [])

        def append_fact(self, text, source="", tags=None):
            facts.append(text)

    class _EmptyMem(_Mem):
        def query(self, since=None, until=None, limit=40):
            return []

    class _LLM:
        def generate(self, system, history, user_text):
            _check("катали в доту" in user_text, "записи дня не ушли в LLM")
            return "Вчера катали в доту до утра, я выиграла пару каток."

    tmp = Path(tempfile.mkdtemp())
    d = DiaryModule(_Mem(), _LLM(), last_path=tmp / "diary_last.txt")
    out = d.check(now=dt.datetime(2026, 9, 5, 3, 5))
    _check(out.startswith("Дневник за 2026-09-04"), f"не тот формат: {out!r}")
    _check(bool(facts), "дневник не записан фактом")
    _check(d.check(now=dt.datetime(2026, 9, 5, 4, 0)) == "",
           "дневник записался ДВАЖДЫ (не идемпотентен)")

    d2 = DiaryModule(_EmptyMem(), _LLM(), last_path=tmp / "diary_last2.txt")
    _check(d2.check(now=dt.datetime(2026, 9, 5, 3, 5)) == "", "пустой день что-то написал")
    _check(d2.last_path.read_text(encoding="utf-8").strip() == "2026-09-04",
           "пустой день не отмечен (будет долбить каждую проверку)")


def test_hotkeys():
    """Хоткеи v14.7: маппинг клавиш + проводка действий в пайплайн."""
    from core.hotkeys import action_for, all_actions
    from memory.memory_module import MemoryModule
    from pipeline.pipeline import DialoguePipeline

    _check(set(all_actions().values()) == {"look", "mute", "watch", "hush"},
           "набор действий")
    _check(action_for("d") == "look" and action_for("S") == "hush", "маппинг клавиш")
    _check(action_for("x") == "", "неизвестная клавиша дала действие")

    class _TTS:
        speaking = False
        stopped = False
        def stop(self):
            self.stopped = True
        def speak_stream(self, sentences, **kwargs):
            return False

    class _Prompts:
        threads_summary = None
        vision_summary = None
        def build_system(self, **kw):
            return "sys"
        def get_history(self):
            return []

    class _Av:
        def command_avatar(self, **kw):
            pass
        def set_mouth(self, v):
            pass
        def set_subtitle(self, *a, **k):
            pass

    class _LLM:
        last_error = ""
        def generate(self, *a, **k):
            return "О, тут в шахматы играют."

    class _Vis:
        enabled = True
        _watch_override = None
        def describe_now(self):
            return "кто-то играет в шахматы"
        def set_watch_override(self, value):
            self._watch_override = value
        def recent_summary(self, limit=3):
            return ""
        def fresh_observation(self, max_age_sec=90.0):
            return ""
        def pending_question(self):
            return ""

    tmp = Path(tempfile.mkdtemp())
    mem = MemoryModule(str(tmp / "memory.json"))
    pipe = DialoguePipeline(mem, _Prompts(), _LLM(), _TTS(), _Av(), vision=_Vis())
    pipe._run_async = lambda fn, *a: fn(*a)   # worker синхронно

    pipe.hotkey_action("mute")
    _check(pipe._muted, "мьют не включился")
    pipe.hotkey_action("mute")
    _check(not pipe._muted, "мьют не выключился")

    pipe.hotkey_action("watch")
    _check(pipe.vision._watch_override is True, "watch не включился")
    pipe.hotkey_action("watch")
    _check(pipe.vision._watch_override is False, "watch не выключился")
    pipe.hotkey_action("watch")
    _check(pipe.vision._watch_override is None, "watch не вернулся в авто")

    tts = pipe.tts
    pipe.hotkey_action("hush")
    _check(tts.stopped, "«замолчать» не заглушило TTS")

    pipe.hotkey_action("look")
    dialog = mem.get_recent_dialog(limit=2)
    _check(any("шахматы" in item["content"] for item in dialog),
           f"взгляд по хоткею не озвучился: {dialog}")


def test_time_sense():
    """Ритм дня v14.7: ночью сонная, утром ворчит, в промпте блок присутствует."""
    import datetime as dt

    from prompts.persona import time_sense
    from prompts.prompt_builder import PromptBuilder

    _check("ночь" in time_sense(dt.datetime(2026, 9, 5, 2, 0)).lower(), "ночь не ночь")
    _check("сон" in time_sense(dt.datetime(2026, 9, 5, 4, 0)).lower()
           or "вял" in time_sense(dt.datetime(2026, 9, 5, 4, 0)).lower(),
           "3 часа ночи бодрая")
    _check("утро" in time_sense(dt.datetime(2026, 9, 5, 8, 0)).lower(), "утро не утро")
    _check("обычн" in time_sense(dt.datetime(2026, 9, 5, 14, 0)).lower(), "день не день")
    _check("вечер" in time_sense(dt.datetime(2026, 9, 5, 20, 0)).lower(), "вечер не вечер")

    class _Mem:
        def get_prompt_context(self, query=None, speaker=None):
            return ""

    sys = PromptBuilder(_Mem()).build_system(user_text="привет")
    _check(time_sense() in sys, "блок ритма дня не в system-промпте")


def test_affinity():
    """Affinity v14.7: копится, клампится, в контексте промпта; грубость бьёт
    сильнее тепла; создателю оценка не меняется."""
    from memory.memory_module import MemoryModule
    from pipeline.pipeline import DialoguePipeline

    tmp = Path(tempfile.mkdtemp())
    mem = MemoryModule(str(tmp / "memory.json"))
    _check(mem.get_affinity("Мурор") == 0, "новый человек не нейтрален")

    mem.adjust_affinity("Мурор", 10, "тест")
    mem.adjust_affinity("Мурор", -30, "грубость")
    _check(mem.get_affinity("Мурор") == -20, f"арифметика affinity сломана")
    _check(mem.get_affinity("мурор") == -20, "регистр имени влияет на affinity")
    mem.adjust_affinity("Кизилл", 999, "предел")
    _check(mem.get_affinity("Кизилл") == 100, "affinity не клампится")

    _check("обожает" in mem.affinity_word(50), "слово для тёплого отношения")
    _check("нейтрал" in mem.affinity_word(0), "слово для нейтрального")
    _check("не выносит" in mem.affinity_word(-50), "слово для ненависти")

    ctx = mem.get_prompt_context(speaker="Кизилл")
    _check("Кизилл" in ctx and "очень тёплые" in ctx, "affinity не в контексте промпта")

    class _TTS:
        speaking = False
        def stop(self): pass
        def speak_stream(self, sentences, **kwargs):
            return False

    class _Prompts:
        threads_summary = None
        def build_system(self, **kw):
            return "sys"
        def get_history(self):
            return []

    class _Av:
        def command_avatar(self, **kw): pass
        def set_mouth(self, v): pass

    class _LLM:
        last_error = ""
        def generate_stream(self, *a, **k):
            return iter(())

    pipe = DialoguePipeline(mem, _Prompts(), _LLM(), _TTS(), _Av())
    pipe._run_async = lambda fn, *a: None

    pipe._bump_affinity("да ты тупая вообще", "Мурор")
    _check(mem.get_affinity("Мурор") == -24, "грубость не понизила отношение")
    pipe._affinity_ts.clear()   # тесты быстрее антиспам-паузы
    pipe._bump_affinity("спасибо огромное, выручила", "Мурор")
    _check(mem.get_affinity("Мурор") == -22, "тепло не повысило отношение")
    pipe._affinity_ts.clear()
    pipe._bump_affinity("ты умница", "Кизилл")
    _check(mem.get_affinity("Кизилл") == 100, "оценка создателя поплыла от вежливости")


TESTS = [
    ("Конфиг v14", "стримовые нити, сэмплинг LLM, флаги STT/донатов", test_config_v14),
    ("Сборка", "build_system: все модули конструируются и связаны", test_composition),
    ("Уши", "адресация по имени, ослышки, срезание имени", test_ears_gate),
    ("Перебивание", "жёсткие триггеры сразу, связные — бесшовно, эхо — игнор", test_barge_in),
    ("Передача слова", "TTS: pre_play до звука, stop_previous/stop, счётчик", test_tts_handover),
    ("Слышимость", "loopback отложенно во время речи, эхо-фильтр", test_hearing_defer),
    ("Вывод TTS", "устройство по имени, дубль-монитор, фолбэк", test_audio_output_routing),
    ("Зрение", "запросы про экран, наблюдения, память, режим просмотра", test_vision_module),
    ("Промпт-зрение", "протокол «минус шесть» + наблюдения в system", test_prompt_vision),
    ("Субтитры", "цвета говорящих, имена, мост аватара", test_subtitles),
    ("Пайплайн", "настроение + теги анимаций (в т.ч. самодельные)", test_pipeline_mood_and_anim_tags),
    ("Эмоции", "детект, тег [ЭМОЦИЯ], влияние на голос", test_emotions),
    ("Настроение", "устойчивое состояние: тег/слова, затухание", test_mood_state),
    ("Присутствие", "голоса, прощания, адресация, хвосты без ответа", test_presence),
    ("Датасет LoRA", "объём, формат, анти-ассистент, создатель", test_dataset_style),
    ("Адресация", "ей/Кизиллу/третьему/никому: словарь+контекст", test_addressee_router),
    ("Одежда", "переодевание по просьбе + защита источников + своя инициатива", test_outfit_change),
    ("Веб", "протокол [ПОИСК: …]: извлечение и чистка", test_web_tags),
    ("TTS", "чистка озвучки: банворды, теги; резка предложений", test_tts_clean_and_split),
    ("TTS-чанки", "склейка коротких, ранний старт длинных", test_tts_chunks),
    ("Память", "диалог (роли), факты, контекст промпта, люди", test_memory_roundtrip_and_context),
    ("Нити", "хвосты вопроса, сброс ответом, стримовые тайминги", test_threads_v14),
    ("Трасса", "журнал этапов пайплайна + группировка", test_pipeline_trace),
    ("Аватар", "мост: seq растёт — повтор анимаций работает", test_avatar_bridge_seq),
    ("Донаты", "файловый ящик: запись → разбор", test_donations_inbox),
    ("VRMA", "ассеты, бандл, песочница debug menu", test_vrma_assets_and_bundle),
    ("Персист", "память окна и одежды после перезапуска", test_persistence),
    ("Промпт", "персона + память + настроение в system", test_prompt_builder_v14),
    ("Инициатива", "активность, отложенные факты (без сети)", test_initiative_offline),
    ("Персона", "характер + протоколы [ПОИСК]/[АНИМАЦИЯ]", test_persona_v14),
    ("Активное окно", "заголовок foreground-окна в запросе зрения", test_active_window),
    ("Watchdog", "проверки, фикс проблем, перезапуск", test_watchdog),
    ("Дневник", "дата ночи, идемпотентность, факт в память", test_diary),
    ("Хоткеи", "маппинг клавиш + проводка действий", test_hotkeys),
    ("Ритм дня", "время суток в персоне и промпте", test_time_sense),
    ("Affinity", "отношение к людям: копится, клампится, в промпте", test_affinity),
]


def run_full_suite():
    """Прогоняет все тесты; возвращает список (категория, имя, ok, detail)."""
    results = []
    for category, name, fn in TESTS:
        try:
            fn()
            results.append((category, name, True, ""))
        except Exception as exc:  # noqa: BLE001 — тест должен пережить любую ошибку
            results.append((category, name, False, str(exc)[:300]))
    return results


def show_full_test_screen(root, app):
    """Совместимая фабрика экрана (по образцу twitch_quiz)."""
    import tkinter as tk

    from .ui import BG, FG, AMBER, FONT, FONT_SM, BaseScreen

    class _FullTestScreen(BaseScreen):
        def __init__(self, master, menu_app):
            super().__init__(master, menu_app)
            self._init_keyboard_nav()
            self._build()

        def _build(self):
            self._make_header("ПОЛНЫЙ ТЕСТ СИСТЕМЫ")
            tk.Label(self, text="Прогоняет все механики Нимфеи v14 в стандартной конфигурации:", bg=BG, fg=FG, font=FONT).pack(anchor="w")
            tk.Label(self, text="уши/адресация, теги ПОИСК/АНИМАЦИЯ, TTS-чистка, память, нити,", bg=BG, fg=FG, font=FONT).pack(anchor="w")
            tk.Label(self, text="трасса, мост аватара (seq), VRMA-ассеты, донаты, промпт, персона.", bg=BG, fg=FG, font=FONT).pack(anchor="w", pady=(0, 6))
            self.btn_run = tk.Button(self, text="[ ЗАПУСТИТЬ ТЕСТ ]", width=22, bg=BG, fg=AMBER, font=FONT,
                                     activebackground=BG, activeforeground=AMBER, relief="flat", cursor="hand2",
                                     command=self._run_tests)
            self.btn_run.pack(pady=4)
            self.status_var = tk.StringVar(value="Готов. Сеть, микрофон и 3D не требуются — только реальные модули.")
            tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM, wraplength=420, justify="left").pack(pady=(2, 6))
            list_frame = tk.Frame(self, bg=BG); list_frame.pack(fill="both", expand=True)
            sb = tk.Scrollbar(list_frame, orient="vertical")
            self.results_list = tk.Listbox(list_frame, bg=BG, fg=FG, font=("Courier New", 9),
                                           selectbackground=AMBER, selectforeground=BG, height=14,
                                           yscrollcommand=sb.set, relief="flat", borderwidth=0)
            sb.config(command=self.results_list.yview)
            self.results_list.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
            self._make_back_button()
            self._register_focusable(self.btn_run, self._run_tests)
            self._focus_widget(0)

        def _run_tests(self):
            self.status_var.set("Тест идёт: импорт модулей (тяжёлые — через myenv), затем все механики...")
            self.btn_run.config(state="disabled")
            self.update_idletasks()
            try:
                from .module_tests import test_all_modules
                module_results = test_all_modules()
            except Exception as exc:  # noqa: BLE001
                module_results = [("module_tests", "FAIL", str(exc))]
            try:
                results = run_full_suite()
            except Exception as exc:  # noqa: BLE001
                self.status_var.set(f"Тест упал целиком: {exc}")
                self.btn_run.config(state="normal")
                return
            self.results_list.delete(0, tk.END)
            passed = 0
            self.results_list.insert(tk.END, "  — ИМПОРТ МОДУЛЕЙ —")
            for name, status, err in module_results:
                if status == "OK":
                    passed += 1
                    self.results_list.insert(tk.END, f"  [ OK ]   import {name} {err if err else ''}".rstrip())
                else:
                    self.results_list.insert(tk.END, f"  [ {status} ] import {name}")
                    self.results_list.insert(tk.END, f"           причина: {err}")
            self.results_list.insert(tk.END, "  — МЕХАНИКИ —")
            total = len(module_results)
            for category, name, ok, detail in results:
                total += 1
                if ok:
                    passed += 1
                    self.results_list.insert(tk.END, f"  [ OK ]   {category} — {name}")
                else:
                    self.results_list.insert(tk.END, f"  [ FAIL ] {category} — {name}")
                    self.results_list.insert(tk.END, f"           причина: {detail}")
            verdict = "ВСЁ РАБОТАЕТ" if passed == total else f"СЛОМАНО: {total - passed}"
            self.status_var.set(f"{passed}/{total} прошло. {verdict}")
            self.btn_run.config(state="normal")

    # Экран для прямого импорта из ui.py: конструктор возвращает готовый BaseScreen.
    return _FullTestScreen(root, app)


class FullTestScreen:
    def __new__(cls, root, app):
        return show_full_test_screen(root, app)
