"""Диалоговый конвейер v14: вход → гейт адресации → LLM-стрим → TTS-стрим.

Правила пайплайна:
  • СТРИМИНГ: LLM отвечает чанками, ответ режется на предложения на лету,
    первое предложение синтезируется, пока модель говорит дальше → первый
    звук ≤2–3 с (модель НЕ меняется — ускоряем обвесы);
  • [ПОИСК: запрос] — протокол интернета: если модель попросила поиск,
    пайплайн ищет и даёт ей второй ход с результатами;
  • гейт адресации (ears): голосовой вход отвечает только на «Нима…»,
    неадресованное — в память как подслушанное;
  • новая реплика ПРЕРЫВАЕТ озвучку; пока Нимфея говорит — оба канала STT
    на паузе (защита от самоуслышивания: и микрофон, и loopback);
  • анти-галлюцинации: дедуп начала ответа против последних реплик;
  • нити разговора (threads): вопрос без ответа → переспрос, реплика без
    реакции → повтор с претензией;
  • трасса этапов → logs/pipeline_trace.jsonl (debug menu → ТРАССА).
"""
from __future__ import annotations

import logging
import os
import random
import re
import threading
import time
from pathlib import Path
from collections import deque

import core.pipeline_trace as trace
from core.config import (BARGE_ECHO_OVERLAP, BARGE_MIN_SHARED, BARGE_MIN_WORDS,
                         BARGE_TRIGGERS, BARGE_WINDOW_SEC, CREATOR_NAME,
                         ECHO_LOOP_OVERLAP, GREETING_ANIM_COOLDOWN_SEC,
                         VOICE_ID_USER,
                         EARS_DIALOG_WINDOW_SEC, EARS_ENABLED, EARS_LLM_ADDRESSEE,
                         EARS_MODE, OUTFIT_SELF_CHANCE, OUTFIT_SELF_COOLDOWN_SEC,
                         RESPONSE_CLEANUP, RESPONSE_MAX_SENTENCES,
                         VISION_INITIATIVE_CHANCE)
from ears.ears_module import (classify_addressee, detect_addressee,
                              is_addressed, is_questionish, strip_all_names,
                              strip_name)
from pipeline.mood_state import MoodState
from pipeline.outfits import (OUTFIT_LABELS, OUTFIT_RANDOM, TRUSTED_SOURCES,
                              detect_outfit_request, fallback_remark,
                              pick_random_outfit)
from pipeline.presence import PresenceTracker
from pipeline.threads import ConversationThreads, is_question
from vision.vision_module import is_screen_request
from web.web_module import extract_search_tag, format_results, search, strip_search_tags

log = logging.getLogger("pipeline")

# «Нима: …» / «Нимфея: …» — прямая озвучка мимо LLM
_DIRECT_RE = re.compile(r"^\s*(?:нимфея|нима|nima)\s*:\s*(.+)$", re.IGNORECASE)

# жёсткие триггеры перебивания — глушат речь мгновенно (частично даже до конца фразы)
_BARGE_HARD_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(t) for t in BARGE_TRIGGERS) + r")(?!\w)",
    re.IGNORECASE)
# зачистка триггеров/обращения из фразы, с которой пойдём к LLM
_BARGE_STRIP_RE = re.compile(
    r"(?<!\w)(" + "|".join(re.escape(t) for t in BARGE_TRIGGERS) + r")(?!\w)[,.!]*\s*",
    re.IGNORECASE)

# лёгкий детектор настроения ответа → mood аватара. Ключи матчатся по границам
# слов (урок: «ого» ловилось внутри «погоды» → ложный excitement)
_MOOD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("anger", ("дура", "заткнись", "бесишь", "съела тебя", "злюсь", "ах ты", "тварь",
               "отвали", "отвали", "достал", "бесит", "тупая")),
    ("sadness", ("грустно", "жаль", "обидно", "прости", "скучаю", "плачу")),
    ("joy", ("ха", "ахаха", "смешно", "обожаю", "ура", "милашка", "котик", "люблю")),
    ("excitement", ("ого", "вау", "отпад", "не может быть", "круто", "дай", "обалдеть")),
    ("fear", ("страшно", "боюсь", "жутко", "ужас")),
    ("shyness", ("стесняюсь", "стесняешься", "стеснялась", "смущена", "смущение",
                 "смущена", "не смотри", "ну ты и", "конфуз")),
    ("arousal", ("разденься", "раздевайся", "раздеться", "секс", "эрот",
                 "возбуждена", "возбудила", "стоны", "стонала", "стонет",
                 "обними", "поцелуй", "прижми", "груди", "грудь", "обнажена")),
    ("interest", ("расскажи", "почему", "как так", "интересно", "подробнее")),
    ("boredom", ("скучно", "скука", "неинтересно")),
    ("sleeping", ("спокойной ночи", "пойду спать", "усну")),
]
_MOOD_RES = [(mood, [re.compile(rf"(?<!\w){re.escape(k)}(?!\w)", re.IGNORECASE) for k in keys])
             for mood, keys in _MOOD_RULES]

# [ЭМОЦИЯ: …] — модель сама помечает своё состояние (персона это требует);
# иначе настроение ловится по словам из ответа
_EMOTION_TAG_RE = re.compile(r"\[ЭМОЦИЯ:\s*([^\]]+)\]", re.IGNORECASE)
_EMOTION_ALIASES = {
    "злость": "anger", "злится": "anger", "злой": "anger", "anger": "anger",
    "радость": "joy", "счастье": "joy", "joy": "joy", "happy": "joy",
    "грусть": "sadness", "sadness": "sadness", "sad": "sadness",
    "стеснение": "shyness", "смущение": "shyness", "shyness": "shyness", "shy": "shyness",
    "возбуждение": "arousal", "arousal": "arousal",
    "восторг": "excitement", "excitement": "excitement",
    "интерес": "interest", "interest": "interest",
    "раздумья": "thinking", "думает": "thinking", "thinking": "thinking",
    "страх": "fear", "fear": "fear",
    "скука": "boredom", "boredom": "boredom",
    "безразличие": "indifference", "indifference": "indifference",
    "нейтрально": "normal", "normal": "normal",
}

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")

# быстрые словесные триггеры «нужен интернет» — фильтр перед микроклассификатором
# (урок 14.09: nimfea v7 не ставит тег [ПОИСК] и выдумывает актуальные факты)
_WEB_HINT_RE = re.compile(
    r"поищи|погугли|нагугли|поиск(ай)?|найди в интернете|курс|погода|новост|который час|"
    r"сколько времени|сколько лет|сколько стоит|цена|кто так(ой|ая)|кто так(ие|ие)|"
    r"когда вышел|когда выйдет|кто выиграл|кто победил|сч[её]т|расписание|"
    r"в этом году|актуальн|биткоин|что за (фильм|игра|сериал)|прогноз", re.IGNORECASE)
# служебные слова реплики, мусор в поисковом запросе
_WEB_NOISE_RE = re.compile(
    r"\bнима\b|\bнимфея\b|\bфея\b|поищи|погугли|нагугли|поиск(ай)?|"
    r"в интернете|найди( в интернете)?|пожалуйста|мне", re.IGNORECASE)

# задержка до подмены VRM после старта показа (крутится — переодевается);
# 0 = сразу (используется тестами)
OUTFIT_SWITCH_DELAY = 1.6

# [АНИМАЦИЯ: имя] — Нимфея сама запускает анимацию аватара в подходящий момент
_ANIM_RE = re.compile(r"\[АНИМАЦИЯ:\s*([^\]]+)\]", re.IGNORECASE)
# 3b-модель любит выдумывать формат («[ТАНЦУЮ]», «[STRETCH]») — ловим по словам
_ANIM_LOOSE_RE = re.compile(
    r"\[([^\]]{0,25}(?:тан|прыг|прыж|маш|помаш|приветств|круж|верт|потяг|"
    r"хлоп|аплод|смущ|румян|зл|груст|удив|зев|сонн|оглян|осмотр|расслаб|"
    r"задум|размышл|"
    r"dance|jump|wave|greeting|spin|stretch|angry|blush|clap|goodbye|"
    r"lookaround|relax|sad|sleepy|surprised|thinking)[^\]]{0,15})\]", re.IGNORECASE)
_ANIM_ALIASES = {
    "greeting": "greeting", "приветствие": "greeting", "помаши": "wave",
    "wave": "wave", "dance": "dance", "танец": "dance", "танцуй": "dance",
    "jump": "jump", "прыжок": "jump", "прыгни": "jump",
    "spinning": "spinning", "кружись": "spinning", "покружись": "spinning",
    "stretch": "stretch", "потянись": "stretch",
    # v14.8.39: все анимации из vita_avatar_app/animations/
    "angry": "angry", "злость": "angry", "злится": "angry", "раздражение": "angry",
    "blush": "blush", "смущение": "blush", "румянец": "blush",
    "clapping": "clapping", "аплодисменты": "clapping", "хлопки": "clapping",
    "goodbye": "goodbye", "прощание": "goodbye", "попрощайся": "goodbye",
    "lookaround": "lookaround", "осмотрись": "lookaround", "оглядись": "lookaround",
    "relax": "relax", "расслабься": "relax", "отдохни": "relax",
    "sad": "sad", "грусть": "sad", "загрусти": "sad",
    "sleepy": "sleepy", "сонность": "sleepy", "зевни": "sleepy",
    "surprised": "surprised", "удивление": "surprised", "удивись": "surprised",
    "thinking": "thinking", "задумчивость": "thinking", "задумайся": "thinking",
}

# Фразы-триггеры: императивы пользователя запускают анимацию МГНОВЕННО, мимо
# LLM (пока модель думает — она уже танцует). Кулдаун на каждое действие —
# защита от зацикливания, когда loopback/чат ловит ту же команду в ответе.
_ANIM_PHRASES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?<!\w)(?:потанцуй|танцуй|потанцуем|станцуй|попляши|спляши|затанцуй)(?!\w)", re.IGNORECASE), "dance"),
    (re.compile(r"(?<!\w)(?:прыгни|попрыгай|подпрыгни)(?!\w)", re.IGNORECASE), "jump"),
    (re.compile(r"(?<!\w)(?:помаши|помаши мне)(?!\w)", re.IGNORECASE), "wave"),
    (re.compile(r"(?<!\w)(?:попрощайся|прощай|до встречи|покедова)(?!\w)", re.IGNORECASE), "goodbye"),
    (re.compile(r"(?<!\w)(?:похлопай|захлопай|аплодируй|поаплодируй)(?!\w)", re.IGNORECASE), "clapping"),
    (re.compile(r"(?<!\w)(?:покружись|закружись)(?!\w)", re.IGNORECASE), "spinning"),
    (re.compile(r"(?<!\w)(?:потянись|потяни)(?!\w)", re.IGNORECASE), "stretch"),
    (re.compile(r"(?<!\w)(?:оглянись|осмотрись|посмотри вокруг)(?!\w)", re.IGNORECASE), "lookaround"),
    (re.compile(r"(?<!\w)(?:расслабься|отдохни)(?!\w)", re.IGNORECASE), "relax"),
    (re.compile(r"(?<!\w)(?:удиви меня|удивись|сделай удивлённое лицо)(?!\w)", re.IGNORECASE), "surprised"),
    (re.compile(r"(?<!\w)(?:задумайся|сделай задумчивое лицо)(?!\w)", re.IGNORECASE), "thinking"),
    (re.compile(r"(?<!\w)(?:позлись|изобрази злость|сделай злое лицо)(?!\w)", re.IGNORECASE), "angry"),
    (re.compile(r"(?<!\w)(?:засмущайся|сделай смущённое лицо|покрасней)(?!\w)", re.IGNORECASE), "blush"),
    (re.compile(r"(?<!\w)(?:погрусти|изобрази грусть|сделай грустное лицо)(?!\w)", re.IGNORECASE), "sad"),
    (re.compile(r"(?<!\w)(?:зевни|изобрази сонность|сделай сонное лицо)(?!\w)", re.IGNORECASE), "sleepy"),
]
_ANIM_PHRASE_COOLDOWN_SEC = 15.0
_DONATION_DEBOUNCE_SEC = 4.0   # шторм донатов копим и благодарим одним ходом
# короткое прощание — тоже триггер goodbye, но ОНО ЖЕ сигнал присутствия,
# поэтому отдельная частая фраза «пока» без контекста не в списке
_GREET_RE = re.compile(
    r"\b(привет|приветик|здравствуй|здравствуйте|хай|хеллоу|доброе утро|"
    r"добрый день|добрый вечер)\b", re.IGNORECASE)

# «меня зовут Вася» / «его зовут Вася» — закрепление имени за голосом (знакомство)
_INTRODUCE_RE = re.compile(
    r"(?<!\w)(?:меня\s+зовут|мо[её]\s+имя|е[её][гё]?\s+зовут)\s+"
    r"([A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё-]{1,20})", re.IGNORECASE)

# Как часто Нима может спрашивать имя у ОДНОГО незнакомого голоса (сек).
ASK_NAME_COOLDOWN_SEC = float(os.environ.get("NIMA_ASK_NAME_COOLDOWN", "900"))

# 4b-модель из тренировочных данных любит выкрикивать случайные имена
# («Ивета!», «Привет, Тимур!»), которых нет ни в контексте, ни в разговоре —
# живой тест 2026-09-11. Обращение-имя, которого нет в system-промпте (там все
# реальные собеседники), вырезается из озвучки/памяти; знакомые не трогаем.
_VOC_HEAD_RE = re.compile(r"^([A-ЯЁ][а-яё]{2,15})(?=[!,]|\s|$)")
_VOC_TAIL_RE = re.compile(r",\s*([А-ЯЁ][а-яё]{2,15})\s*([.!?…]+)\s*$")
_GREETING_WORD_RE = re.compile(
    r"\b(привет|приветик|здравствуй|здоров|салют|хай|доброе утро|"
    r"добрый день|добрый вечер)\b", re.IGNORECASE)
# обычные слова-открывашки, которые НЕ имена — их фильтр не трогает никогда
_VOC_NOT_NAME_STEMS = (
    "стоп", "слушай", "погоди", "секунд", "минут", "так", "ну", "ладно",
    "ясно", "понятно", "отлично", "здорово", "круто", "классно", "привет",
    "приветик", "ого", "вау", "блин", "ага", "эй", "алло", "хай", "окей",
    "ок", "всё", "все", "хватит", "стой", "тише", "чувак", "бро", "народ",
    "друзья", "девочки", "мальчики", "ребята", "смотри", "знаешь", "серьёзно",
    "серьезно", "офиг", "дальше", "начнём", "погнали", "поехали", "давай",
)

_GATED_SOURCES = ("stt", "loop")   # голосовые каналы проходят через гёт адресации

# «слабые» слова: одни лишь они в пересечении с монологом — ещё не повод
# перебивать («а ты вчера…» vs любой монолог про вчера)
_BARGE_WEAK_WORDS = {
    "вчера", "сегодня", "сейчас", "потом", "когда", "весь", "вся", "всё",
    "вот", "такой", "такая", "просто", "очень", "больше", "меньше", "давай",
    "ладно", "может", "нужно", "можно", "первый", "потом", "теперь",
}

# Субтитры (v14.6): Нима — красная обводка, Кизилл (мик/консоль) — синяя,
# остальные — случайный цвет на человека (даже если она его знает)
_SUB_NIMA = "#ff4b4b"
_SUB_ME = "#4da6ff"

# Affinity (v14.7): отношение к человеку меняют его слова. Тепло копится,
# грубость бьёт сильнее (как в жизни: нажить симпатию труднее, чем потерять).
_AFFINITY_POS_RE = re.compile(
    r"(?<!\w)(?:спасибо|благодар|обожаю|люблю|молодец|умница|ты\s+лучший|"
    r"ты\s+лучшая|крутая|милашка|котик|красавиц|профи)(?!\w)", re.IGNORECASE)
_AFFINITY_NEG_RE = re.compile(
    r"(?<!\w)(?:дура|тупая|заткнись|бесишь|отвали|достал|тварь|"
    r"ненавижу|идиотка|глупая|бесполезн|уродлив|уродина|отстой)(?!\w)", re.IGNORECASE)
_AFFINITY_GAP_SEC = 60.0   # не чаще раза в минуту на человека — не фармится


class DialoguePipeline:
    def __init__(self, memory, prompts, llm, tts, avatar, stt=None, events=None,
                 web=None, initiative=None, threads=None, vision=None) -> None:
        self.memory = memory
        self.prompts = prompts
        self.llm = llm
        self.tts = tts
        self.avatar = avatar
        self.stt = stt                    # микрофонный канал
        self.stt_loop = None              # loopback-канал (подключает start.py)
        self.events = events
        self.web = web
        self.initiative = initiative
        self.vision = vision              # зрение: экран компьютера (vision/)
        if self.vision:
            self.vision.on_observation = self._on_vision
            self.vision.busy_check = self._busy_for_vision
            self.prompts.vision_summary = self.vision.recent_summary
        self.threads = threads or ConversationThreads()
        self.prompts.threads_summary = self.threads.pending_summary
        self.threads.make_follow_up = self.follow_up
        self.mood_state = MoodState()    # устойчивое настроение между репликами
        self.presence = PresenceTracker()  # кто сейчас «в комнате» (сессия)
        self.threads.on_tail_dropped = self.presence.on_no_answer
        self._dialog_speaker: str | None = None    # кому она последней отвечала
        self._dialog_window_until = 0.0            # …и сколько его ход без имени
        if self.initiative:
            self.initiative.on_topic = self.proactive
            self.initiative.busy_check = self._busy_for_initiative
        self._worker: threading.Thread | None = None
        self._gen_cancel = threading.Event()   # новая реплика отменяет генерацию старой
        self._stop = threading.Event()
        # донатный шторм: копим DONATION_DEBOUNCE_SEC и благодарим одним ходом
        self._donation_buffer: list[tuple[str, str, str]] = []
        self._donation_timer: threading.Timer | None = None
        self._donation_lock = threading.Lock()
        self.last_stream_error = ""
        self._monologue: list[str] = []           # что она сейчас озвучивает (для перебивания)
        self._barge_window_until = 0.0            # после «Нима!»/стопа — ответ без имени
        self._outfit_init_until = 0.0             # кулдаун собственной смены образа
        self._outfit_remarks: deque[str] = deque(maxlen=10)  # анти-повтор реплик про одежду
        self._outfit_fallback_idx = 0
        self._say_seq = 0                         # поколение озвучки: старый _say не
                                                  # восстанавливает слух поверх нового
        self._last_monologue: list[str] = []      # что озвучивала последней — эхо-фильтр loopback
        self._last_greeting_anim = 0.0            # когда последний раз машала приветствием
        self._last_phrase_anim: dict[str, float] = {}  # кулдауны фраз-триггеров анимаций
        self._sub_colors: dict[str, str] = {}     # случайный цвет субтитров на человека (сессия)
        self._muted = False                       # хоткей мьюта ушей
        self._affinity_ts: dict[str, float] = {}  # антиспам affinity по человеку
        self._asked_name_ts: dict[str, float] = {}  # когда последней спрашивала имя (по голосу)

    # ================= субтитры =================
    def _subtitle_style(self, speaker: str | None, source: str) -> tuple[str, str]:
        """(цвет обводки, имя) субтитра. Нима — красный, Кизилл (микрофон/
        консоль) — синий без имени, остальные — случайный цвет на человека:
        даже известный из памяти человек каждый сеанс получает новый цвет."""
        if source == "nima":
            return _SUB_NIMA, ""
        if source in ("mic", "manual") and (not speaker or speaker == VOICE_ID_USER):
            return _SUB_ME, ""
        key = (speaker or source).strip().lower()
        if key not in self._sub_colors:
            self._sub_colors[key] = f"hsl({random.randint(0, 359)}, 90%, 65%)"
        return self._sub_colors[key], (speaker or "Голос")

    def _show_user_subtitle(self, text: str, source: str,
                            speaker: str | None) -> None:
        setter = getattr(self.avatar, "set_subtitle", None)
        if setter:
            color, name = self._subtitle_style(speaker, source)
            try:
                setter(text, color, name)
            except Exception:  # noqa: BLE001
                log.exception("[ERROR] субтитр не отправлен")

    # ================= зрение: экран =================
    def _busy_for_vision(self) -> bool:
        """Фон-зрение ждёт, пока она говорит или пайплайн генерирует ответ:
        Ollama одна — нельзя конкурировать с репликой за генерацию."""
        return self.tts.speaking or bool(self._worker and self._worker.is_alive()
                                         and self._worker is not threading.current_thread())

    def _busy_for_initiative(self) -> bool:
        """Инициатива не стартует поверх TTS, ответа или ожидающего хвоста."""
        worker_busy = bool(self._worker and self._worker.is_alive()
                           and self._worker is not threading.current_thread())
        try:
            pending = bool(self.threads.pending_summary())
        except Exception:  # noqa: BLE001
            pending = False
        return self.tts.speaking or worker_busy or pending

    def _on_vision(self, text: str, kind: str) -> None:
        """Наблюдение фона. Факты уже в памяти (RAG); вопрос про недогляд —
        кандидат в инициативы (она сама переспросит у комнаты)."""
        if kind == "comment":
            if self._busy_for_vision():
                log.info("vision: комментарий отменён pipeline busy")
                return
            self.proactive("vision", text)
        elif kind == "question" and self.initiative:
            self.initiative.add_fact(f"[с экрана, не разглядела] {text}")
            log.info("vision: вопрос про экран ушёл в инициативы: %r", text[:60])

    # ================= входящие реплики =================
    def _web_search_query(self, text: str) -> str | None:
        """Нужен ли для ответа интернет (v14.8.47). Только по ключевым словам:
        микроклассификатор на nimfea v7 ненадёжен (живой тест 02:15: на
        «нужны ли свежие данные?» отвечал «90»/«44», игнорируя инструкцию).
        Ложное срабатывание стоит 2-4 с поиска, ложный пропуск — выдуманный
        факт, поэтому список триггеров широкий. Возвращает запрос или None."""
        if not _WEB_HINT_RE.search(text):
            return None
        # служебные слова не должны уходить в поисковик (урок 14.09:
        # «Нима, поищи в интернете: …» искалась как есть)
        query = strip_search_tags(text)
        query = _WEB_NOISE_RE.sub(" ", query)
        query = query.strip(" :-,.")
        return query or None

    def _fix_known_names(self, text: str) -> str:
        """Чинит исковерканные STT имена известных людей (урок v14.8.24: модель
        base втрое быстрее, но «Кизилл» превращает в «Яки, Зилл»). Каждое слово
        (≥4 букв, не имя целиком) сравнивается с известными именами; близкое
        (difflib ≥0.66) заменяется на каноничное. Только для речи из STT —
        ручной ввод и чат не трогаем."""
        import difflib

        try:
            known = set(self.memory.person_names())
        except Exception:  # noqa: BLE001 — тестовая память без метода
            return text
        known.add(CREATOR_NAME)
        known.update(("Нима", "Нимфея"))
        known_low = {k.lower() for k in known if k}
        if not known_low:
            return text
        out = []
        for word in text.split():
            core = word.strip(",.!?…-—\"'")
            low = core.lower()
            if len(core) >= 4 and low not in known_low:
                match = difflib.get_close_matches(low, known_low, n=1, cutoff=0.66)
                if match:
                    canonical = next(k for k in known if k.lower() == match[0])
                    word = word.replace(core, canonical)
                    log.info("STT: имя поправлено: %r → %r", core, canonical)
            out.append(word)
        return " ".join(out)

    def handle_user_text(self, text: str, source: str = "stt",
                         speaker: str | None = None, force: bool = False,
                         seamless: bool = False, request_id: str | None = None) -> None:
        text = text.strip()
        if not text:
            return
        if source in ("stt", "loop"):
            text = self._fix_known_names(text)
        # отложенный слух loopback (матрица слышимости): сказанное во время её
        # речи. Бо́льшая часть буфера — её собственный голос из динамиков:
        # пересекается с монологом → эхо, игнор.
        # ИСКЛЮЧЕНИЕ (v14.7.5): если в реплике есть ПРЯМОЕ обращение по имени
        # («Нима, …»), это точно НЕ её эхо (она себя по имени не зовёт) — друг
        # из созвона обращается к ней. Раньше такие фразы глушились эхо-фильтром,
        # если частично пересекались с её монологом → «не слышит по имени».
        if source == "loop" and not force and self._last_monologue \
                and len(text.split()) >= 3 and not is_addressed(text, self.llm):
            echo = self._words_overlap(text, " ".join(self._last_monologue))
            if echo >= ECHO_LOOP_OVERLAP:
                log.info("ears: отложенное эхо её речи (%.2f) — игнор: %r",
                         echo, text[:60])
                return
        if self.initiative:
            # Эхо уже отброшено выше. Чат ставит короткий anti-interrupt gate,
            # а валидная речь/ручной ввод сбрасывают основную тишину.
            self.initiative.note_activity("chat" if source == "twitch" else "user")
        if self.vision:
            self.vision.note_user()
        self._show_user_subtitle(text, source, speaker)

        # окно перебивания: после «Нима!»/«стоп» следующий вопрос имени не требует
        if (not force and source in _GATED_SOURCES
                and not is_addressed(text, self.llm)
                and time.time() < self._barge_window_until):
            log.info("ears: окно перебивания — имя не требуется: %r", text[:60])
            force = True
        self._barge_window_until = 0.0

        if source == "manual":
            direct = _DIRECT_RE.match(text)
            if direct:
                self._speak_only(direct.group(1).strip())
                return
            if text.lower().startswith("!regen "):
                text = text[7:].strip()

        # гейт адресации: голосовой вход — только реплики, адресованные ей.
        # Маршрутизатор различает «ей / Кизиллу / третьему / никому»:
        # чужие реплики живут в памяти как подслышанные, говорящий — в presence.
        # ИСКЛЮЧЕНИЕ: реплика ПРО ЭКРАН («да блин где эта кнопка…») — человек
        # говорит «сам себе», но она видит экран, поэтому отвечает.
        if not force and source in _GATED_SOURCES:
            addressee = self._route_addressee(text, speaker)
            screen_call = (self.vision and self.vision.enabled
                           and is_screen_request(text))
            if addressee != "nima" and not screen_call:
                log.info("ears: реплика адресована %s (%s/%s): %r",
                         addressee or "никому", source, speaker, text[:60])
                from core.config import EARS_OVERHEAR_TO_MEMORY
                if EARS_OVERHEAR_TO_MEMORY:
                    who = f"{source}:overheard" + (f":{addressee}" if addressee else "")
                    self.memory.append_dialog("user", text, user=who,
                                              speaker=speaker)
                try:  # голос слышен → человек всё равно присутствует
                    self.presence.on_voice(speaker, text)
                except Exception:  # noqa: BLE001
                    pass
                return
            if screen_call and addressee != "nima":
                log.info("vision: реплика про экран — отвечаю без имени: %r", text[:60])
        text = strip_name(text) if source in _GATED_SOURCES else text
        if source in _GATED_SOURCES:
            self.threads.on_user_reply(text)  # ответ получен — хвост закрыт
        trace.mark("route_done", request_id, info=f"{source}/{speaker or '-'}") if request_id else None
        self._run_async(self._respond, text, source, speaker, seamless, request_id)

    def _route_addressee(self, text: str, speaker: str | None) -> str | None:
        """Кому адресована голосовая реплика: 'nima' | имя человека | None.

        Слой 1 — словарь (звательное имя в начале / её имя в тексте);
        слой 2 — контекст: она ждёт ответа от этого голоса (хвост-вопрос) или
        только что отвечала ему (окно диалога); слой 3 — LLM-классификация
        неоднозначных вопросительных фраз. 'strict'-режим = старое поведение.
        """
        if not EARS_ENABLED or EARS_MODE not in ("smart",):
            return "nima" if is_addressed(text, self.llm) else None
        known = set(self.presence.present_names())
        try:
            known |= self.memory.person_names()
        except Exception:  # noqa: BLE001 — у тестовой памяти может не быть метода
            pass
        layer1 = detect_addressee(text, known)
        if layer1:
            return layer1
        low = (speaker or "").strip().lower()
        if low:
            if self.threads.pending_question_for(speaker):
                return "nima"   # она ждёт ответ именно от этого голоса
            if (self._dialog_speaker or "").strip().lower() == low \
                    and time.time() < self._dialog_window_until:
                return "nima"   # продолжение диалога с ней — имя не нужно
        if EARS_LLM_ADDRESSEE and self.llm is not None \
                and is_questionish(text) and len(text) <= 160:
            addr = classify_addressee(text, speaker, self.presence.present_names(),
                                      self.llm)
            if addr:
                log.info("ears: LLM определил адресата: %s", addr)
                return addr
        return None

    # ================= перебивание (barge-in) =================
    def handle_barge_partial(self, text: str) -> None:
        """Недоговорённая фраза (частичный STT-скан во время её речи): мгновенно
        глушим только на жёстких триггерах — «стоп» не должен ждать конца фразы."""
        if self.tts.speaking and _BARGE_HARD_RE.search(text or ""):
            log.info("barge: жёсткий триггер в недоговорённой фразе: %r", text[:60])
            self.tts.stop()

    def handle_barge_in(self, text: str, channel: str,
                        speaker: str | None = None) -> None:
        """Речь пользователя, пока Нимфея говорит. Три исхода:
        • жёсткий триггер («стоп!») — молчит МГНОВЕННО;
        • фраза обращена к ней или связана с монологом — бесшовная передача
          слова (v14.5): она ДОГОВАРИВАЕТ, пока новый ответ синтезируется,
          и первый звук новой реплики идёт сразу после глушения старой —
          без мёртвой тишины на генерацию;
        • посторонняя болтовня — игнор, она продолжает."""
        text = (text or "").strip()
        if not text:
            return
        mono = " ".join(self._monologue)
        echo = self._words_overlap(text, mono) if mono else 0.0

        if echo >= BARGE_ECHO_OVERLAP and not _BARGE_HARD_RE.search(text):
            log.info("barge: похоже на эхо её речи (%.2f) — игнор: %r", echo, text[:60])
            return

        hard = bool(_BARGE_HARD_RE.search(text))
        if hard:
            # жёсткий триггер глушит речь СРАЗУ, без ожиданий классификатора
            log.info("barge: жёсткий триггер: %r", text[:60])
            self.tts.stop()
        else:
            if channel == "mic":
                # владелец с микрофона перебивает всегда (урок 13.09: гейт
                # «не связано с монологом» не давал ему вставить слово)
                addressed, related = True, True
            else:
                addressed = is_addressed(text, self.llm)
                related = self._related_to_monologue(text)
                if not (addressed or related):
                    log.info("barge: не связано с монологом (%.2f) — продолжает говорить", echo)
                    return
            log.info("barge: перебивание бесшовно (addr=%s rel=%.2f): %r",
                     addressed, echo, text[:60])

        stripped = _BARGE_STRIP_RE.sub("", text).strip(" ,.!")
        if not stripped or not strip_all_names(stripped):
            # голое «Нима!» / «стоп» — она замолкает и отдаёт слово
            self._barge_window_until = time.time() + BARGE_WINDOW_SEC
            return
        self.handle_user_text(stripped, source=channel, speaker=speaker,
                              force=True, seamless=not hard)

    @staticmethod
    def _words_overlap(text: str, reference: str) -> float:
        """Доля содержательных слов фразы, встречающихся в монологе (эхо ≈ 1)."""
        from memory.memory_module import MemoryModule
        ta = MemoryModule.normalize_text(text)
        tb = MemoryModule.normalize_text(reference)
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta)

    def _related_to_monologue(self, text: str) -> bool:
        """Фраза связана с тем, что она сейчас озвучивает: есть общие значимые
        слова (не из «слабого» списка) и фраза достаточно длинная."""
        from memory.memory_module import MemoryModule
        if len(text.split()) < BARGE_MIN_WORDS or not self._monologue:
            return False
        shared = MemoryModule.normalize_text(text) & \
            MemoryModule.normalize_text(" ".join(self._monologue))
        return len(shared - _BARGE_WEAK_WORDS) >= BARGE_MIN_SHARED

    def handle_donation(self, name: str, message: str, amount: str = "") -> None:
        """Донат/сообщение стрима — адресовано ей по умолчанию.

        Шторм донатов (урок 14.09: 10 штук залпом) НЕ обрабатываем по одному:
        каждая новая реплика отменяла генерацию предыдущей — до 22 с тишины
        и «LLM вернула пустоту». Копим буфер DONATION_DEBOUNCE_SEC и
        благодарим за весь залп одним ходом."""
        with self._donation_lock:
            self._donation_buffer.append((name, message, amount))
            if self._donation_timer and self._donation_timer.is_alive():
                return
            timer = threading.Timer(_DONATION_DEBOUNCE_SEC, self._flush_donations)
            timer.daemon = True
            self._donation_timer = timer
        timer.start()

    def _flush_donations(self) -> None:
        with self._donation_lock:
            batch, self._donation_buffer = self._donation_buffer, []
        if not batch:
            return
        self.avatar.command_avatar(action="dance")  # благодарит в движении
        if len(batch) == 1:
            name, message, amount = batch[0]
            amount_note = f" ({amount})" if amount else ""
            text = f"[Донат от {name}{amount_note}] {message}"
            speaker = name
        else:
            lines = [f"{i}. {n}{f' ({a})' if a else ''}: {m}"
                     for i, (n, m, a) in enumerate(batch, 1)]
            text = ("Пришла пачка донатов — поблагодари всех разом, коротко, "
                    "в своём тоне, по именам, где уместно:\n" + "\n".join(lines))
            speaker = batch[-1][0]
        self.handle_user_text(text, "donation", speaker=speaker)

    # ================= affinity: отношение к людям =================
    def _bump_affinity(self, text: str, speaker: str | None) -> None:
        """Слова человека меняют её отношение (v14.7). Создатель не участвует:
        его место в персоне фиксировано и не колеблется от вежливости."""
        if not speaker or speaker == VOICE_ID_USER:
            return
        now = time.time()
        if now - self._affinity_ts.get(speaker, 0.0) < _AFFINITY_GAP_SEC:
            return
        if _AFFINITY_NEG_RE.search(text):
            delta, reason = -4, "грубость"
        elif _AFFINITY_POS_RE.search(text):
            delta, reason = 2, "тепло/благодарность"
        else:
            return
        self._affinity_ts[speaker] = now
        try:
            score = self.memory.adjust_affinity(speaker, delta, reason)
            log.info("affinity: %s %+d → %d", speaker, delta, score)
        except Exception:  # noqa: BLE001 — у тестовой памяти может не быть метода
            log.exception("[ERROR] affinity не обновлён")

    # ================= действия глобальных хоткеев (v14.7) =================
    def hotkey_action(self, action: str) -> None:
        """Ctrl+Alt+D/M/W/S из core/hotkeys — проводка в логику."""
        if action == "look":
            self.look_now()
        elif action == "mute":
            self.mute_toggle()
        elif action == "watch":
            if self.vision:
                cur = self.vision._watch_override
                nxt = {None: True, True: False, False: None}[cur]
                self.vision.set_watch_override(nxt)
        elif action == "hush":
            self.tts.stop()
        else:
            log.warning("[WARNING] неизвестное действие хоткея: %r", action)

    def look_now(self) -> None:
        """«Глянь на экран» по хоткею: свежий кадр + короткий комментарий вслух."""
        if not (self.vision and self.vision.enabled):
            return
        if self.tts.speaking:
            self._interrupt_speech()

        def work() -> None:
            rid = trace.new_request()
            seen = self.vision.describe_now()
            if not seen:
                return
            trace.mark("postprocess", rid, info="хоткей: взгляд на экран")
            extra = (f"Ты ТОЛЬКО ЧТО взглянула на экран и видишь: {seen}. Скажи вслух "
                     "коротко (1-2 предложения), что происходит, в своём тоне; "
                     "не разглядела деталь — честно скажи (зрение минус шесть).")
            system = self.prompts.build_system(user_text=None, extra=extra)
            reply = self.llm.generate(system, [], "Прокомментируй, что на экране.")
            if not reply:
                return
            self._say(iter([reply]), source="nima:vision", request_id=rid)
            self.memory.append_dialog("bot", reply, user="hotkey:look")

        self._run_async(work)

    def mute_toggle(self) -> bool:
        """Мьют/анмьют ушей (микрофон + loopback). Возвращает новое состояние."""
        self._muted = not self._muted
        self._pause_listening(self._muted)
        log.info("хоткей: уши %s", "ЗАГЛУШЕНЫ" if self._muted else "включены")
        return self._muted

    # ================= одежда =================
    def _avatar_outfit(self) -> str | None:
        current = getattr(self.avatar, "current_outfit", None)
        if not current:
            state = getattr(self.avatar, "_state", None)
            current = state.get("current_outfit") if isinstance(state, dict) else None
        return current

    def _outfit_change_flow(self, requested: str, text: str,
                            speaker: str | None, rid: str) -> None:
        """Смена образа: показ (кружится) + реплика + переодевание посреди оборота.

        Защита источника — на вызывающей стороне (_respond: только
        TRUSTED_SOURCES). Голое/сексуальное — только по явной просьбе
        (модуль outfits), в случайный выбор не попадают.
        """
        current = self._avatar_outfit()
        outfit = pick_random_outfit(current) if requested == OUTFIT_RANDOM else requested
        if not outfit:
            return
        self._outfit_init_until = time.time() + OUTFIT_SELF_COOLDOWN_SEC
        trace.mark("outfit", rid, info=f"{current} → {outfit}")
        log.info("outfit: смена образа %s → %s", current, outfit)
        self._interrupt_speech()
        self.avatar.command_avatar(action="spinning")     # показ образа — покружилась
        remark = self._outfit_remark(outfit, asked=bool(text))
        mood_box = ["normal"]
        remark = self._apply_emotion(remark, mood_box)
        remark = self._fire_animations(remark) or remark
        if outfit != current:
            # переодевание посреди оборота: рендерер подменит VRM, пока она крутится
            if OUTFIT_SWITCH_DELAY > 0:
                timer = threading.Timer(
                    OUTFIT_SWITCH_DELAY, lambda: self.avatar.command_avatar(outfit=outfit))
                timer.daemon = True
                timer.start()
            else:
                self.avatar.command_avatar(outfit=outfit)
        self._say(iter([remark]), speaker=speaker, source="outfit",
                  mood_box=mood_box, request_id=rid)
        self.memory.append_dialog("user", text or "[сама сменила образ]",
                                  user="outfit", speaker=speaker)
        self.memory.append_dialog("bot", remark, user="outfit")

    def _outfit_remark(self, outfit: str, asked: bool) -> str:
        """Реплика про переодевание: генерится LLM, повторы отсеиваются."""
        label = OUTFIT_LABELS.get(outfit, "новый образ")
        if asked:
            task = ("Тебя попросили сменить одежду, и ты переоделась — теперь на тебе "
                    f"{label}. Отреагируй одной-двумя живыми фразами в своём характере. "
                    "Без скобок, тегов и эмодзи. Не повторяй свои прошлые фразы "
                    "про переодевание.")
        else:
            task = ("Ты сама решила сменить образ и переоделась — теперь на тебе "
                    f"{label}. Скажи об этом одной-двумя живыми фразами в своём "
                    "характере. Без скобок, тегов и эмодзи. Не повторяй свои прошлые "
                    "фразы про переодевание.")
        system = self.prompts.build_system(user_text=None, extra=task)
        history = self.prompts.get_history()
        remark = ""
        for _ in range(3):
            candidate = re.sub(r"\[[^\]]{0,60}\]", "",
                               (self.llm.generate(system, history, task) or "")).strip()
            if len(candidate) < 3:
                continue
            remark = candidate
            if all(_overlap(remark.lower(), prev.lower()) < 0.55
                   for prev in self._outfit_remarks):
                break
        if not remark:
            remark = fallback_remark(self._outfit_fallback_idx)
        self._outfit_fallback_idx += 1
        self._outfit_remarks.append(remark)
        return remark

    def _maybe_self_outfit_change(self) -> None:
        """Редкая собственная инициатива: сама решила сменить образ
        (повседневный пул, шанс OUTFIT_SELF_CHANCE, кулдаун)."""
        if self.tts.speaking or time.time() < self._outfit_init_until:
            return
        if random.random() >= OUTFIT_SELF_CHANCE:
            return
        if not pick_random_outfit(self._avatar_outfit()):
            return
        log.info("outfit: собственная инициатива — решила сменить образ")
        self._outfit_change_flow(OUTFIT_RANDOM, "", None, trace.new_request())

    def proactive(self, source: str, topic: str) -> None:
        """Живая инициатива: сама завела разговор (initiative/)."""
        if self.tts.speaking:
            return  # не перебивать чужую речь инициативой
        # внутренняя реплика: не отменяет ответ пользователю (урок 14.09:
        # гонка busy-проверки — комментарий зрения/инициатива проскакивал
        # между проверкой и стартом и глушил начатый ответ на вопрос)
        self._run_async(self._proactive, source, topic, priority="internal")

    def follow_up(self, tail) -> None:
        """Нить разговора: переспросить / повторить с претензией (threads/)."""
        self._run_async(self._follow_up, tail, priority="internal")

    # ================= ответ LLM + озвучка =================
    def _ask_name_note(self, speaker: str | None) -> str:
        """Незнакомый голос (заглушка «Друг N»/«Собеседник») → попросить
        представиться. Не чаще раза в ASK_NAME_COOLDOWN_SEC на голос — иначе
        каждый ответ превращается в допрос (фактически имя она спросит один
        раз за знакомство; после представления голос переименуется)."""
        from memory.memory_module import PLACEHOLDER_NAME_RE
        if not speaker or not PLACEHOLDER_NAME_RE.match(speaker.strip()):
            return ""
        now = time.time()
        if now - self._asked_name_ts.get(speaker, 0.0) < ASK_NAME_COOLDOWN_SEC:
            return ""
        self._asked_name_ts[speaker] = now
        log.info("voice_id: спрашиваю имя у незнакомого голоса %s", speaker.strip())
        return (f"Ты ещё не знаешь имя этого человека (голос помечен как «{speaker.strip()}»). "
                "В этом ответе естественно, в своей манере, спроси, как его зовут.")

    def _maybe_rename_speaker(self, text: str, speaker: str | None) -> str | None:
        """«Меня зовут Вася» / «его зовут Вася» → закрепить имя за голосом.

        Работает ТОЛЬКО для заглушек («Собеседник», «Друг N»): если голос уже
        опознан, имя ему не переписываем. Своё имя и имя создателя принимать
        нельзя: титул «создатель» нетранслируемый (см. core.config)."""
        from memory.memory_module import PLACEHOLDER_NAME_RE
        if not speaker or not PLACEHOLDER_NAME_RE.match(speaker.strip()):
            return None
        match = _INTRODUCE_RE.search(text or "")
        if not match:
            return None
        name = match.group(1).strip(" -")
        if not name:
            return None
        name = name[0].upper() + name[1:]
        if name.lower() in {"нима", "нимфея", "nima", "nimfea"}:
            return None
        if name.lower() == CREATOR_NAME.lower():
            log.info("voice_id: попытка присвоить голосу имя создателя — отклонено")
            return None
        try:
            if self.memory.rename_person(speaker, name):
                log.info("voice_id: голос %s теперь %s", speaker, name)
                return name
        except Exception:  # noqa: BLE001 — у тестовой памяти может не быть метода
            pass
        return None

    @staticmethod
    def _strip_unknown_names(sentence: str, system: str) -> str:
        """Обращение-имя, которого нет в system-промпте, вырезается.
        Хвост («Сочувствую, Юля.») и голову-выкрик («Ивета!»); для головы
        дополнительно разрешаем хвост-приветствие («Ивета, приветик»).
        Знакомые имена (есть в system хотя бы в другом регистре) и обычные
        слова-открывашки не трогаем."""
        low = system.lower()
        match = _VOC_TAIL_RE.search(sentence)
        if match and match.group(1).lower() not in low:
            sentence = sentence[:match.start()].rstrip() + match.group(2)
        match = _VOC_HEAD_RE.match(sentence)
        if match and match.group(1).lower() not in low \
                and not match.group(1).lower().startswith(_VOC_NOT_NAME_STEMS):
            rest = sentence[match.end():].strip(" ,!-")
            if not rest or _GREETING_WORD_RE.match(rest):
                sentence = rest if not rest else rest[0].upper() + rest[1:]
        return sentence

    def _respond(self, text: str, source: str, speaker: str | None,
                 seamless: bool = False, request_id: str | None = None) -> None:
        rid = request_id or trace.new_request()
        trace.mark("in", rid, text=text, info=f"{source}/{speaker or '-'}")

        # знакомство: «меня зовут X» от заглушки → имя закрепляется за голосом
        new_name = self._maybe_rename_speaker(text, speaker)
        if new_name:
            speaker = new_name
            try:
                self.presence.on_voice(speaker, text)
            except Exception:  # noqa: BLE001
                pass

        # одежда: просьба переодеться — ТОЛЬКО от стримера (микрофон/консоль).
        # Чат/донаты/loopback мимо детектора: отказ в разговоре — дело персоны.
        if source in TRUSTED_SOURCES:
            requested = detect_outfit_request(text)
            if requested:
                self._outfit_change_flow(requested, text, speaker, rid)
                return

        # бесшовное перебивание: НЕ глушим текущую речь — она доигрывает, пока
        # новый ответ генерируется; глушение произойдёт перед первым звуком
        if not seamless:
            self._interrupt_speech()
        phrase_action = self._phrase_anim(text)
        if phrase_action:
            # императив («потанцуй») — анимация сразу, «thinking» не нужен:
            # он бы перебил one-shot кроссфейдом
            self.avatar.command_avatar(action=phrase_action)
        else:
            self.avatar.command_avatar(action="thinking")
        if _GREET_RE.search(text) and source not in ("threads",) \
                and self._greeting_animation_ok(text, source):
            self.avatar.command_avatar(action="greeting")

        # настроение: устойчивое состояние (теги [ЭМОЦИЯ] прошлых ответов +
        # заражение от слов пользователя) — его получает и промпт, и голос
        self.mood_state.observe_user(_detect_mood(text))
        # присутствие: голос распознан → человек тут; прощание/«X ушёл» → нет
        try:
            self.presence.on_voice(speaker, text, self.memory.person_names())
        except Exception:  # noqa: BLE001 — у тестовых памяти может не быть метода
            pass
        # affinity: тепло/грубость человека копятся в отношении к нему
        self._bump_affinity(text, speaker)

        # зрение: реплика про экран («что я делаю?», «где кнопка?») → свежий
        # кадр в промпт (~2-4 с: захват <0.3 с + короткая генерация gemma3)
        screen_note = ""
        if self.vision and self.vision.enabled and is_screen_request(text):
            trace.mark("postprocess", rid, info="зрение: свежий кадр")
            seen = self.vision.describe_now()
            if seen:
                screen_note = (f"Ты ТОЛЬКО ЧТО взглянула на экран и видишь: {seen}. "
                               "Отвечай по тому, что видно; если не разобрала "
                               "деталь — честно скажи (зрение минус шесть) и спроси.")

        mood_box = [self.mood_state.mood]   # меняется на лету: [ЭМОЦИЯ: …] из ответа
        # поиск ДО ответа (урок 14.09: nimfea v7 не ставит [ПОИСК: …] и вместо
        # «не знаю» выдумывает курс/погоду — проверено живьём и few-shot'ом).
        # Микроклассификатор решает, нужен ли интернет; результаты идут в system.
        web_note = ""
        if self.web and source not in ("threads", "initiative"):
            query = self._web_search_query(text)
            if query:
                trace.mark("postprocess", rid, info=f"поиск до ответа: {query}")
                try:
                    results = search(query)
                    block = format_results(query, results)
                except Exception as exc:  # noqa: BLE001 — интернет не отвечает
                    log.warning("web: поиск до ответа упал: %s", exc)
                    block = ""
                if block:
                    web_note = (f"Ты поискала в интернете «{query}» и нашла:\n{block}\n"
                                "Найди в результатах самый конкретный ответ (цифры) "
                                "и перескажи его в 1 предложении своим тоном. "
                                "Ничего не выдумывай сверх результатов; если ответа "
                                "в результатах нет — честно скажи, что не нашла.")
                    log.info("web: поиск до ответа: %s", query[:60])
        extra = " ".join(s for s in (screen_note, web_note, self._ask_name_note(speaker)) if s)
        system = self.prompts.build_system(user_text=text, mood=self.mood_state.mood,
                                           speaker=speaker,
                                           present=self.presence.present_names(),
                                           extra=extra)
        history = self.prompts.get_history(speaker=speaker)
        trace.mark("prompt", rid, info=f"{len(system)} зн., истории {len(history)}")

        trace.mark("llm_start", rid)
        sentences = self._reply_sentences(system, history, text, rid, mood_box=mood_box)
        first = self._first_not_duplicate(sentences, rid)
        if first is not None:
            trace.mark("first_sentence", rid, text=first)
        stub = first is None
        if stub:
            if self._gen_cancel.is_set():
                # генерацию отменили новой репликой — пустота здесь ожидаема,
                # заглушку НЕ говорим (урок 14.09: в донатном шторме она
                # успевала проскочить между отменами)
                log.info("реплика отменена — заглушка не нужна")
                return
            first = "Что-то голова пустая… повтори, а?"
            log.warning("[WARNING] LLM вернула пустоту (%s) — заглушка", self.llm.last_error)

        parts: list[str] = []
        reply_gen = self._collecting(_chain(first, sentences), parts)
        trace.mark("llm_done", rid, text=first[:100])
        pre_play = self._handover if (seamless and self.tts.speaking) else None
        self._say(reply_gen, speaker=speaker, mood_box=mood_box, request_id=rid,
                  pre_play=pre_play)

        reply = " ".join(part.strip() for part in parts if part.strip())
        self.memory.append_dialog("user", text, user=source, speaker=speaker)
        if not stub and reply:
            # заглушка таймаута — не её слова: в память/нити её не пишем,
            # иначе модель потом «вспоминает», что так говорила
            self.memory.append_dialog("bot", reply, user=source)
            if source in _GATED_SOURCES:
                self.threads.on_bot_reply(reply, speaker or "", source,
                                          is_question=is_question(reply))
        if self.initiative:
            self.initiative.note_spoke()
            for hit in search_parts(reply):  # найденный ответ — кандидат для инициативы
                self.initiative.add_fact(hit)
        self._maybe_self_outfit_change()
        self.mood_state.tick()   # ход прошёл — хвост настроения затухает
        if speaker:
            # окно диалога: его следующий ход не требует её имени
            self._dialog_speaker = speaker
            self._dialog_window_until = time.time() + EARS_DIALOG_WINDOW_SEC

    def _reply_sentences(self, system: str, history: list[dict], text: str, rid: str,
                         allow_search: bool = True, mood_box=None):
        """Генератор ПРЕДЛОЖЕНИЙ ответа. [ПОИСК: …] → веб-поиск → второй ход.
        [ЭМОЦИЯ: …] → живое настроение (голос + лицо) без перегенерации.

        Стриминг: yield по завершённым предложениям, пока модель генерит дальше.
        """
        buffer = ""
        pending_query: str | None = None
        try:
            first_token = True
            for token in self.llm.generate_stream(system, history, text):
                if self._gen_cancel.is_set():
                    log.info("реплика отменена новой — LLM-стрим остановлен")
                    return
                if first_token:
                    trace.mark("first_token", rid)
                    first_token = False
                buffer += token
                while True:
                    match = _SENTENCE_RE.search(buffer)
                    if not match:
                        break
                    sentence, buffer = buffer[:match.end()].strip(), buffer[match.end():]
                    query = extract_search_tag(sentence)
                    if query:
                        pending_query = query
                        continue  # тег не озвучивается
                    sentence = self._apply_emotion(sentence, mood_box)
                    sentence = self._fire_animations(sentence)
                    sentence = self._strip_unknown_names(sentence, system)
                    if sentence:
                        yield sentence
            tail = buffer.strip()
            query = extract_search_tag(tail)
            if query:
                pending_query = query
                tail = strip_search_tags(tail)
            tail = self._apply_emotion(tail, mood_box)
            tail = self._fire_animations(tail)
            tail = self._strip_unknown_names(tail, system)
            if tail:
                yield tail
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] стрим LLM: %s", exc)
            self.last_stream_error = str(exc)

        if pending_query and allow_search and self.web:
            trace.mark("postprocess", rid, info=f"поиск: {pending_query}")
            results = search(pending_query)
            block = format_results(pending_query, results) or \
                f"Поиск «{pending_query}» ничего не дал или интернет не отвечает."
            system2 = self.prompts.build_system(user_text=pending_query,
                                                mood=self.mood_state.mood,
                                                extra=block)
            history2 = history + [
                {"role": "user", "content": text},
                {"role": "assistant", "content": f"[ПОИСК: {pending_query}]"},
            ]
            yield from self._reply_sentences(system2, history2, pending_query, rid,
                                             allow_search=False, mood_box=mood_box)
        elif pending_query:
            yield (f"Интернет-поиск у меня сейчас не отвечает, так что честно скажу: "
                   f"не знаю про «{pending_query}». Расскажешь?")

    def _apply_emotion(self, sentence: str, mood_box) -> str:
        """[ЭМОЦИЯ: X] из ответа → настроение в mood_box (голос+лицо), тег из озвучки.

        Заодно фиксирует своё состояние в mood_state — оно переживёт этот ответ
        и на следующих ходах попадёт в промпт (LLM продолжит в этом настроении).
        """
        if not mood_box:
            return sentence
        match = _EMOTION_TAG_RE.search(sentence)
        if not match:
            return sentence
        mood = _EMOTION_ALIASES.get(match.group(1).strip().lower())
        if mood:
            mood_box[0] = mood
            self.mood_state.observe_tag(mood)
            log.info("эмоция из тега модели: %s", mood)
        return _EMOTION_TAG_RE.sub("", sentence).strip()

    def _phrase_anim(self, text: str) -> str | None:
        """Императив пользователя («потанцуй», «похлопай») → анимация сразу,
        мимо LLM. Кулдаун на каждое действие: loopback мог услышать её ответ
        с тем же словом, а чатерсы любят повторять команду. None — не фраза-
        команда, пойдёт обычный ход с «thinking»."""
        now = time.time()
        for pattern, action in _ANIM_PHRASES:
            if not pattern.search(text):
                continue
            if now - self._last_phrase_anim.get(action, 0.0) < _ANIM_PHRASE_COOLDOWN_SEC:
                log.info("анимация %s по фразе — кулдаун, скип", action)
                return None
            self._last_phrase_anim[action] = now
            log.info("анимация по фразе пользователя: %s", action)
            return action
        return None

    def _greeting_animation_ok(self, text: str, source: str) -> bool:
        """Защита от спама анимацией-приветствием (живой прогон 11.09: VRMA_03
        каждые 15–30 с, 843 проигрываний за сессию). Ложные срабатывания —
        СВОЙ голос с loopback (эхо её же «привет», в т.ч. из ответов) и «привет»
        внутри чужой длинной фразы. Пропускаем только короткое приветствие от
        собеседника, не чаще GREETING_ANIM_COOLDOWN_SEC."""
        if source == "loop" and self.tts.speaking:
            return False   # она сейчас говорит → loopback слышит её саму
        if len(text.split()) > 6:
            return False   # приветствие — короткая реплика, не фраза с «привет» внутри
        now = time.time()
        if now - self._last_greeting_anim < GREETING_ANIM_COOLDOWN_SEC:
            return False
        self._last_greeting_anim = now
        log.info("анимация: приветствие (реплика %s)", source)
        return True

    def _fire_animations(self, sentence: str) -> str:
        """[АНИМАЦИЯ: имя] (и самодельные «[ТАНЦУЮ]») → команда аватару; тег из озвучки уходит."""
        action = None
        match = _ANIM_RE.search(sentence)
        if match:
            action = _ANIM_ALIASES.get(match.group(1).strip().lower())
            sentence = _ANIM_RE.sub("", sentence)
        else:
            match = _ANIM_LOOSE_RE.search(sentence)
            if match:
                words = match.group(1).lower()
                for key, act in (("тан", "dance"), ("dance", "dance"),
                                 ("прыг", "jump"), ("прыж", "jump"), ("jump", "jump"),
                                 ("маш", "wave"), ("wave", "wave"),
                                 ("приветств", "greeting"), ("greeting", "greeting"),
                                 ("круж", "spinning"), ("верт", "spinning"), ("spin", "spinning"),
                                 ("потяг", "stretch"), ("stretch", "stretch"),
                                 ("хлоп", "clapping"), ("аплод", "clapping"), ("clap", "clapping"),
                                 ("смущ", "blush"), ("румян", "blush"), ("blush", "blush"),
                                 ("злост", "angry"), ("злит", "angry"), ("злый", "angry"),
                                 ("angry", "angry"),
                                 ("груст", "sad"), ("sad", "sad"),
                                 ("удив", "surprised"), ("surprised", "surprised"),
                                 ("зев", "sleepy"), ("сонн", "sleepy"), ("sleepy", "sleepy"),
                                 ("оглян", "lookaround"), ("осмотр", "lookaround"),
                                 ("lookaround", "lookaround"),
                                 ("расслаб", "relax"), ("отдохн", "relax"), ("relax", "relax"),
                                 ("задум", "thinking"), ("thinking", "thinking"),
                                 ("проща", "goodbye"), ("goodbye", "goodbye")):
                    if key in words:
                        action = act
                        break
                sentence = _ANIM_LOOSE_RE.sub("", sentence)
        if action:
            log.info("анимация по тегу из ответа: %s", action)
            self.avatar.command_avatar(action=action)
        return sentence.strip()

    def _first_not_duplicate(self, sentences, rid: str) -> str | None:
        """Первое предложение, не повторяющее последние ответы (анти-залипание).

        Пропускаем только: почти дословный повтор (overlap > 0.9) или короткую
        словесную заусенцу ≤4 слов («Поняла.», «Ох.»), уже звучавшую. Раньше
        выбрасывались ЛЮБЫЕ первые 1-2 предложения с overlap > 0.8 — ответы
        начинались с середины мысли («Всё, теперь можно обедать» без начала)."""
        recent = self.memory.get_recent_dialog(limit=3)
        recent_first = [item["content"].split(".")[0][:120].lower()
                        for item in recent if item["role"] == "assistant"]
        for _ in range(3):
            sentence = next(sentences, None)
            if sentence is None:
                return None
            if extract_search_tag(sentence):
                return sentence  # тег обработает _reply_sentences
            low = sentence.lower()[:120]
            dup = max((_overlap(low, prev) for prev in recent_first), default=0.0)
            if dup > 0.9 or (dup > 0.8 and len(sentence.split()) <= 4):
                log.info("pipeline: дедуп пропустил дубль: %r", sentence[:60])
                trace.mark("postprocess", rid, info="дедуп: дубль")
                continue
            return sentence
        return None

    @staticmethod
    def _clean_sentences(gen):
        """Cheap streaming cleanup: drop near-duplicate sentences and cap rambling."""
        if not RESPONSE_CLEANUP:
            yield from gen
            return
        kept: list[str] = []
        for sentence in gen:
            sentence = re.sub(r"\s+", " ", sentence).strip()
            if not sentence:
                continue
            low = sentence.lower()
            if any(_overlap(low, prev.lower()) > 0.82 for prev in kept):
                continue
            kept.append(sentence)
            yield sentence
            if RESPONSE_MAX_SENTENCES > 0 and len(kept) >= RESPONSE_MAX_SENTENCES:
                return

    @staticmethod
    def _collecting(gen, parts: list[str]):
        for sentence in DialoguePipeline._clean_sentences(gen):
            parts.append(sentence)
            yield sentence

    def _speak_only(self, text: str) -> None:
        self._interrupt_speech()
        self.memory.append_dialog("bot", text, user="manual")
        self._say(iter([text]), speaker=None, source="manual")

    # ================= озвучка =================
    def _say(self, sentences, speaker: str | None = None, source: str = "nima:llm",
             mood_box=None, request_id: str | None = None,
             pre_play=None) -> None:
        """Стриминговая озвучка генератора предложений.

        Слух во время речи (v14.5): loopback в режиме отложенного слуха —
        буфер копится и распознаётся после реплики (её собственный голос
        выкинет эхо-фильтр), микрофон остаётся в режиме перебивания.
        mood_box: если эмоция сменилась тегом модели — лицо и голос обновляются
        на лету, прямо посреди реплики.
        pre_play: бесшовное перебивание (v14.5) — колбэк глушит ПРЕДЫДУЩУЮ речь
        ровно перед первым звуком этой (пробел между репликами ≈ 0).

        Поколение (_say_seq): заглушённая старая реплика НЕ восстанавливает
        слух/аватара поверх новой — это делает только последняя озвучка.
        """
        if mood_box is None:
            mood_box = ["normal"]
        self._say_seq += 1
        seq = self._say_seq
        if self.stt_loop:
            self.stt_loop.set_defer(True)   # НЕ глушим: сказанное во время её
                                            # речи распознаётся сразу после неё
        if self.stt:
            self.stt.set_barge(True)
        self._monologue = []
        last_mood = None

        setter = getattr(self.avatar, "set_subtitle", None)

        def tracked():
            nonlocal last_mood
            for sentence in sentences:
                if self._gen_cancel.is_set():
                    log.info("реплика отменена новой — озвучка прекращена")
                    return
                self._monologue.append(sentence)
                # СУБТИТР НЕ ставим здесь: тут предложение только сгенерировано и
                # уходит в очередь синтеза (наперёд), поэтому строка вылезала
                # блоком и ДО голоса. Показ субтитра перенесён в on_sentence —
                # он срабатывает ровно перед озвучкой чанка (v14.7.2).
                if mood_box[0] != last_mood:
                    last_mood = mood_box[0]
                    self.avatar.command_avatar(mood=last_mood)
                    # логика поверх тегов: она засыпает/устала → зевает и
                    # клонится (однократно на смену состояния, не на каждое
                    # предложение)
                    if last_mood == "sleeping":
                        self.avatar.command_avatar(action="sleepy")
                yield sentence

        def on_sentence(chunk):
            # её субтитр — красный, по предложениям (караоке), синхронно с голосом
            if setter:
                try:
                    setter(chunk, _SUB_NIMA)
                except Exception:  # noqa: BLE001
                    pass

        self.avatar.command_avatar(mood=mood_box[0], action="speaking", speaking=True)
        try:
            self.tts.speak_stream(tracked(), trace=trace, request_id=request_id,
                                  mood_box=mood_box, pre_play=pre_play,
                                  on_sentence=on_sentence)
            threading.Event().wait(0.3)  # хвост эха микрофона
        finally:
            self._last_monologue = list(self._monologue)  # для эхо-фильтра loopback
            self._monologue = []
            if seq == self._say_seq:   # она не заглушена новой репликой
                self.avatar.set_mouth(0.0)
                # action НЕ сбрасываем в idle: one-shot VRMA (танец/приветствие)
                # доигрывает сама и возвращает дыхание через finished-событие
                self.avatar.command_avatar(speaking=False)
                if self.stt:
                    self.stt.set_barge(False)
                if self.stt_loop:
                    self.stt_loop.set_defer(False)

    def _handover(self) -> None:
        """Бесшовная передача слова: глушим ПРЕДЫДУЩУЮ речь и ждём её выхода,
        после чего первый чанк новой реплики играет мгновенно."""
        self.tts.stop_previous()
        deadline = time.time() + 5
        while self.tts.active_streams() > 1 and time.time() < deadline \
                and not self._stop.is_set():
            threading.Event().wait(0.02)

    def _pause_listening(self, value: bool) -> None:
        for stt in (self.stt, self.stt_loop):
            if stt:
                stt.pause(value)

    def pause_listening(self, value: bool = True) -> None:
        """Ручное глушение микрофона (например, пока говорит Twitch-донат)."""
        self._pause_listening(value)

    # ================= инициатива и нити =================
    def _proactive(self, source: str, topic: str) -> None:
        # зрение вплетено в инициативы: она комментирует/спрашивает про экран,
        # когда видела что-то свежее, или переспрашивает то, что не разглядела
        if self.vision and self.vision.enabled:
            if random.random() < VISION_INITIATIVE_CHANCE:
                obs = self.vision.fresh_observation(120)
                if obs:
                    source, topic = "vision", obs
            else:
                question = self.vision.pending_question()
                if question:
                    source, topic = "vision_question", question
        # адресность: инициатива обращается к конкретному присутствующему
        addressed = self.presence.pick_addressed()
        lead = {
            "memory": "В комнате давно тихо. Вспомни что-то из блока воспоминаний "
                      "и заведи разговор с этого, коротко.",
            "web": "В комнате давно тихо. Поделись этим фактом с собеседником "
                   "своим тоном, коротко.",
            "question": "В комнате давно тихо. Спроси собеседника — переформулируй "
                        "вопрос по-своему, живо.",
            "develop": "Ты недавно затронула эту тему, но она не исчерпана, и тебе "
                       "хочется её развить. Продолжи с НОВОЙ стороны: добавь деталь "
                       "или историю из воспоминаний, задай встречный вопрос — "
                       "но НЕ повторяй то, что уже сказала. Коротко.",
            "vision": "Ты поглядываешь на экран компьютера. Прокомментируй "
                      "увиденное или задай вопрос про это — коротко и в своём "
                      "тоне, как сидящая рядом подруга.",
            "vision_question": "Ты глянула на экран и не разглядела деталь (зрение "
                               "минус шесть). Спроси у присутствующих, что это "
                               "было, — коротко и в своём тоне.",
        }.get(source, "Заведи короткий разговор сама.")
        if addressed:
            lead += f" Реплика адресована {addressed}: обратись к нему/ней по имени."
        extra = f"{lead}\nТема: {topic}"
        self.avatar.command_avatar(action="greeting")  # заводит разговор — машет
        system = self.prompts.build_system(user_text=None, extra=extra)
        history = self.prompts.get_history()
        parts: list[str] = []
        sentences = self._collecting(self._reply_sentences(system, history,
                                                           topic, trace.new_request(),
                                                           allow_search=False), parts)
        self._say(sentences, speaker=None, source="nima:initiative")
        reply = " ".join(parts).strip()
        if reply:
            self.memory.append_dialog("user", f"[инициатива: {source}] {topic}",
                                      user="initiative")
            self.memory.append_dialog("bot", reply, user="initiative")
            if is_question(reply):
                self.threads.on_bot_reply(reply, "", "initiative", is_question=True)

    def _follow_up(self, tail) -> None:
        kind_note = ("Ты задавала вопрос и пока не получила ответ."
                     if tail.kind == "question" else
                     "Ты что-то сказала, но реакции не было.")
        tone = ("Переспроси коротко." if tail.nudges <= 1 else
                "Повтори с претензией и колкостью, как устаешь повторять.")
        extra = (f"{kind_note} Ты говорила: «{tail.text[:200]}». "
                 f"{tone} Одно-два предложения, без эмодзи и скобок.")
        system = self.prompts.build_system(user_text=None, speaker=tail.speaker or None,
                                           extra=extra)
        reply = self.llm.generate(system, self.prompts.get_history()[-6:],
                                  extra) or tail.text
        self.memory.append_dialog("user", f"[хвост] {tail.text[:120]}", user="threads")
        self.memory.append_dialog("bot", reply, user="threads")
        self._say(iter([reply]), speaker=tail.speaker or None, source="nima:threads")
        if self.initiative:
            self.initiative.note_spoke()

    # ================= служебное =================
    def _interrupt_speech(self) -> None:
        if self.tts.speaking:
            log.info("Реплика перебивает озвучку — стоп TTS")
            self.tts.stop()
            while self.tts.speaking and not self._stop.is_set():
                threading.Event().wait(0.05)

    def _run_async(self, fn, *args, priority: str = "user") -> None:
        """Одна реплика — один worker. НОВАЯ реплика ОТМЕНЯЕТ предыдущую
        («последняя фраза пользователя побеждает»): генерация старой останавливается
        на ближайшей проверке флага (между токенами/предложениями), её TTS глушится.
        Живой тест 13.09: join до 180 с превращал две быстрые реплики подряд в
        очередь — Нима долго молчала и отвечала на устаревший вопрос.

        Ответы всё равно не перемешиваются: старый worker успевает выйти (флаг
        проверяется и в LLM-стриме, и в _say) до старта нового.

        priority="internal" (зрение/инициатива/нити): идущий ответ на реплику
        пользователя не отменяется — внутренняя реплика пропускается. Урок
        14.09: busy-проверка в _on_vision гоняла гонку с _respond, фоновый
        комментарий успевал отменить начатый ответ, и Нима молчала.
        """
        old = self._worker
        if old and old is not threading.current_thread():
            if priority == "internal" and old.is_alive() \
                    and getattr(self, "_worker_priority", "user") == "user":
                log.info("внутренняя реплика пропущена — идёт ответ пользователю")
                return
            self._gen_cancel.set()          # старая генерация — стоп
            if old.is_alive():              # гонка 14.09: join не запущенного треда = RuntimeError
                old.join(timeout=3.0)       # она выйдет по флагу, 3 с — запас
        ev = threading.Event()
        self._gen_cancel = ev

        def run():
            if ev.is_set():
                return
            try:
                fn(*args)
            except Exception:  # noqa: BLE001
                log.exception("[ERROR] пайплайн упал на реплике")
        self._worker = threading.Thread(target=run, daemon=True)
        self._worker_priority = priority
        self._worker.start()

    def poll_manual_queue(self, manual_control) -> None:
        for entry in manual_control.drain_entries():
            cmd = entry.get("cmd")
            if isinstance(cmd, dict):
                self._handle_manual_cmd(cmd)
                continue
            log.info("Ручной ввод из debug menu: %s", entry["text"])
            self.handle_user_text(entry["text"], source="manual")

    # --- структурные команды debug menu ---
    def _handle_manual_cmd(self, cmd: dict) -> None:
        kind = str(cmd.get("cmd", "")).strip()
        if kind == "enroll_voice":
            self._enroll_voice_cmd(cmd)
        elif kind == "delete_voice_profile":
            self._delete_voice_profile_cmd(cmd)
        else:
            log.warning("[WARNING] неизвестная команда debug menu: %r", cmd)

    def _delete_voice_profile_cmd(self, cmd: dict) -> None:
        """Удалить голосовой профиль: {"cmd":"delete_voice_profile","name":…}.
        Защита: профиль создателя (VOICE_ID_USER) удалить нельзя."""
        import json

        from core.config import ENROLL_RESULT_PATH, VOICE_ID_USER

        name = str(cmd.get("name", "")).strip()
        ok = False
        detail = "впиши имя профиля"
        if name:
            if name.strip().lower() == VOICE_ID_USER.strip().lower():
                detail = f"профиль «{VOICE_ID_USER}» удалить нельзя"
            else:
                ok = bool(self.memory.delete_person(name))
                detail = (f"профиль «{name}» удалён" if ok
                          else f"профиль «{name}» не найден")
        payload = {"ok": ok, "name": name, "detail": detail,
                   "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        try:
            ENROLL_RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False),
                                          encoding="utf-8")
        except OSError:
            pass
        log.info("delete_voice_profile: %s (%s)", "ОК" if ok else "НЕТ", detail)

    def _enroll_voice_cmd(self, cmd: dict) -> None:
        """Записать голосовой профиль: {"cmd":"enroll_voice","name":…,"wav":…}.
        Профиль владелец может перезаписывать; чужие имена — только создавать.
        Результат — cache/enroll_result.json (его показывает debug menu)."""
        import json
        import wave

        import numpy as np

        from core.config import ENROLL_RESULT_PATH, VOICE_ID_USER

        def _finish(ok: bool, detail: str) -> None:
            payload = {"ok": ok, "name": cmd.get("name", ""), "detail": detail,
                       "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
            try:
                ENROLL_RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False),
                                              encoding="utf-8")
            except OSError:
                pass
            log.info("enroll_voice: %s (%s)", "ОК" if ok else "ОШИБКА", detail)

        name = str(cmd.get("name", "")).strip() or VOICE_ID_USER
        voice_id = getattr(self, "voice_id", None)
        if voice_id is None:
            _finish(False, "voice_id недоступен в пайплайне")
            return
        wav = Path(str(cmd.get("wav", "")).strip())
        if not wav.is_absolute():
            wav = Path(__file__).resolve().parent.parent / wav
        if not wav.exists():
            _finish(False, f"файл записи не найден: {wav}")
            return
        try:
            with wave.open(str(wav), "rb") as wf:
                rate = wf.getframerate()
                frames = wf.getnframes()
                audio = np.frombuffer(wf.readframes(frames), dtype=np.int16).astype(np.float32) / 32768.0
        except Exception as exc:  # noqa: BLE001
            _finish(False, f"не прочитать wav: {exc}")
            return
        if rate != 16000:
            _finish(False, f"частота записи {rate} Гц — нужна 16000 Гц")
            return
        if frames < 16000:  # короче 1 с
            _finish(False, f"запись слишком короткая ({frames / rate:.1f} с, нужно ≥1 с)")
            return
        ok = voice_id.enroll(name, audio)
        _finish(ok, "профиль голоса записан" if ok
                else "не удалось вычислить эмбеддинг (тишина или ошибка модели)")

    def poll_donations(self, inbox_path) -> None:
        """Файловый ящик донатов data/donations_inbox.jsonl (debug menu →
        ТЕСТ-ДОНАТ и внешние интеграции)."""
        try:
            lines = inbox_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if not lines:
            return
        try:
            inbox_path.unlink()
        except OSError:
            pass
        import json
        for line in lines:
            try:
                don = json.loads(line)
                name = str(don.get("donor") or don.get("name") or "аноним").strip()
                amount = " ".join(x for x in (str(don.get("amount", "")).strip(),
                                              str(don.get("currency", "")).strip()) if x)
                self.handle_donation(name, str(don.get("message", "")), amount)
            except Exception:  # noqa: BLE001
                log.exception("[ERROR] битый донат в ящике")

    def shutdown(self) -> None:
        self._stop.set()
        if self.vision:
            self.vision.stop()
        self.tts.stop()


def _chain(first, rest):
    yield first
    yield from rest


def _detect_mood(text: str) -> str:
    for mood, patterns in _MOOD_RES:
        if any(p.search(text) for p in patterns):
            return mood
    return "normal"


def _overlap(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    from memory.memory_module import MemoryModule
    ta, tb = MemoryModule.normalize_text(a), MemoryModule.normalize_text(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def search_parts(reply: str) -> list[str]:
    """Предложения-кандидаты в «отложенные факты» инициативы (простая эвристика)."""
    return [s.strip() for s in reply.split(". ") if len(s.strip()) > 60][:2]
