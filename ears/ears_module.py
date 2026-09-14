"""ears — гейт адресации: Нимфея СЛЫШИТ всегда, а отвечает только когда
к ней обращаются по имени (решение пользователя, требование 11).

Правила:
  • regex по формам имени («Нимфея, Нима, Ним…») — бесплатно и мгновенно;
  • если имя не найдено, но фраза похожа на обращение (шёпот-опечатки STT:
    «нимффа», «мифея», «Vima»…) — резервный микро-классификатор LLM
    (num_predict=3, сотни миллисекунд, НЕ тормозит отклонённые фразы);
  • неадресованное НЕ теряется: уходит в память как «подслушанное» (требование:
    она живая, чужой разговор в комнате — тоже контекст) и в движок инициативы.

Канал loopback проходит через тот же гейт: друг из созвона тоже должен
позвать её по имени.
"""
from __future__ import annotations

import logging
import re

from core.config import EARS_CLASSIFIER, EARS_ENABLED, EARS_NAMES

log = logging.getLogger("ears")


def _names_regex() -> re.Pattern:
    forms = sorted((re.escape(n) for n in EARS_NAMES), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(forms) + r")\w*", re.IGNORECASE)


_NAME_RE = _names_regex()

# STT часто коверкает имена: «Нимфа»→«Мифа/Нимка/Vima», ловим похожие оболочки
_FUZZY_RE = re.compile(
    r"\b(ним|нúма|мифе?я|мима|нимо|нимфф?[аоы]|nimf?e?[ay]|nima|nyamа)\b", re.IGNORECASE)

_CLASSIFY_SYSTEM = (
    "Ты — фильтр обращений. Определи, обращается ли говорящий к девушке по имени "
    "Нимфея (Нима). Косвенные обращения считаются: «Ним», «Нимф», искажённые формы. "
    "Ответь ровно одним словом: ДА или НЕТ.")


def is_addressed(text: str, llm=None) -> bool:
    """True — фраза адресована Нимфее (regex или резервный классификатор)."""
    if not EARS_ENABLED:
        return True  # гейт выключен — всё адресовано
    if _NAME_RE.search(text):
        return True
    if _FUZZY_RE.search(text):
        return True
    if EARS_CLASSIFIER and llm is not None and len(text) <= 160:
        if _llm_says_addressed(text, llm):
            log.info("ears: классификатор увидел обращение: %r", text)
            return True
    return False


def _llm_says_addressed(text: str, llm) -> bool:
    try:
        reply = llm.generate_micro(_CLASSIFY_SYSTEM, text)
        return bool(reply) and "ДА" in reply.upper()
    except Exception as exc:  # noqa: BLE001 — классификатор не критичен
        log.warning("[WARNING] ears-классификатор: %s", exc)
        return False


def strip_name(text: str) -> str:
    """Убрать обращение из начала фразы, чтобы LLM видел суть: «Нима, что…» → «что…»."""
    stripped = _NAME_RE.sub("", text, count=1)
    stripped = re.sub(r"^[\s,.\-–—!]+", "", stripped)
    return stripped or text.strip()


def strip_all_names(text: str) -> str:
    """Убрать ВСЕ формы имени («Нима, стоп» → «стоп»). Пусто = обращение было
    только именем — перебивание без новой фразы."""
    return re.sub(r"^[\s,.\-–—!]+|[\s,.\-–—!]+$", "", _NAME_RE.sub("", text))


# ================= маршрутизация адресата (v14.4.1) =================
#
# Реплика в комнате может быть адресована: Нимфе, Кизиллу, третьему человеку
# или никому конкретно (болтовня). Роутер трёхслойный — от дешёвого к дорогому:
#   1) словарь: звательное имя в начале («Вася, …») или её имя где угодно;
#   2) контекст (считает pipeline): хвост вопроса, окно диалога;
#   3) LLM-микро-классификация неоднозначных вопросительных фраз.

_ADDR_SYSTEM = (
    "Определи, к кому обращена реплика в комнате. Говорящий и присутствующие "
    "перечислены. Девушка-лиса по имени Нимфея (Нима) — тоже в комнате. "
    "Ответь ровно одним словом: НИМА — если обращаются к Нимфе; ИМЯ — имя "
    "человека из комнаты, если к нему; НИКТО — если ни к кому конкретно.")

_QUESTIONISH_RE = re.compile(
    r"\?|(?<!\w)(как|что|почему|покак|когда|где|кто|зачем|сколько|можешь|"
    r"будешь|какой|какая|какие|правда|серьёзно|а ты|а у тебя)(?!\w)", re.IGNORECASE)


def _first_word(text: str) -> str:
    """Первое слово фразы (без служебных зачинов и пунктуации)."""
    stripped = re.sub(r"^\s*(?:ну|ну-ка|эй|ей|так|смотри|слышь|слышь-ка)\b[\s,.]*",
                      "", text.strip(), flags=re.IGNORECASE)
    match = re.match(r"[а-яёa-z]+", stripped, re.IGNORECASE)
    return match.group(0).lower() if match else ""


def detect_addressee(text: str, known_names) -> str | None:
    """Слой 1: адресат по словарю. 'nima' | имя человека (как в known_names) | None."""
    if not text:
        return None
    first = _first_word(text)
    if first:
        if first.startswith(tuple(n.lower() for n in EARS_NAMES)):
            return "nima"
        best = None
        for name in known_names:
            low = name.lower()
            if first == low or first.startswith(low):
                if best is None or len(low) > len(best.lower()):
                    best = name
        if best:
            return best
    if _NAME_RE.search(text) or _FUZZY_RE.search(text):
        return "nima"   # её имя в тексте — считаем обращением (как раньше)
    return None


def classify_addressee(text: str, speaker: str | None, present, llm) -> str | None:
    """Слой 3: LLM-микро-классификация ('nima' | имя | None). Не критична к сбоям."""
    if llm is None:
        return None
    try:
        prompt = (f"Говорит: {speaker or 'неизвестный голос'}. "
                  f"В комнате: {', '.join(present) if present else 'никого'}. "
                  f"Реплика: «{text[:160]}»")
        reply = llm.generate_micro(_ADDR_SYSTEM, prompt)
        word = (reply or "").strip().lower()
        if not word:
            return None
        if word.startswith("нима") or "нимфе" in word:
            return "nima"
        if "никто" in word or "никуда" in word:
            return None
        for name in present:
            if name.lower() in word:
                return name
        return None
    except Exception as exc:  # noqa: BLE001 — классификация не критична
        log.warning("[WARNING] ears-адресат LLM: %s", exc)
        return None


def is_questionish(text: str) -> bool:
    """Похоже ли на вопрос (когда стоит звать LLM-классификатор адресата)."""
    return bool(_QUESTIONISH_RE.search(text or ""))
