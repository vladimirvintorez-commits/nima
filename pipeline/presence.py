"""Кто сейчас «в комнате»: присутствие людей в разговоре.

Голос распознан (VoiceID) → человек тут. Попрощался → его нет. Кизил сказал,
что кто-то ушёл → его нет. Нима переспросила несколько раз (нить разговора),
ответа нет → человека нет. Всё это состояния РАЗГОВОРА (сессии); память о
самом человеке (имя, черты, голос) живёт в memory.json постоянно.

Адресность: инициативы и реплики должны адресоваться только присутствующим —
имена из present_names() идут в промпт и в проактивные реплики.
"""
from __future__ import annotations

import random
import re
import time

# прощание = человек вышел. «Пока» бывает союзом («пока не поздно») —
# поэтому с негативными lookahead на союзные продолжения
_FAREWELL_RE = re.compile(
    r"(?<!\w)(все?го доброго|до связи|до встречи|до завтра|бай\b|прощай|"
    r"пока(?!\s+(не|буд|ты|мы|он|она|как|занят|думал))|"
    r"я пош[её]л|я пошла|я в аут|я спать|я ухожу|ухожу я|меня нет|мне пора|"
    r"спокойной ночи|я сваливаю|я ливаю|отваливаю|пойду я|я на выход)(?!\w)",
    re.IGNORECASE)

# «X ушёл/ушла/вышел/свалил…» — сказал кто-то другой (обычно Кизил)
_GONE_ABOUT_RE = ("{n}\\s+(уш[её]л|ушла|вышел|вышла|свалил\\w*|отвалил\\w*|"
                  "ливану?л\\w*|попрощал\\w*|оффнулся|ливнул)")


class PresenceTracker:
    """Имена присутствующих + события входа/выхода. Session state."""

    def __init__(self, timeout_sec: float = 0.0) -> None:
        # name.lower() → (display_name, last_seen_ts); timeout_sec=0 — без таймаута
        self._present: dict[str, tuple[str, float]] = {}
        self._timeout_sec = timeout_sec

    # ---------- события ----------

    def on_voice(self, speaker: str | None, text: str) -> None:
        """Реплика от распознанного голоса: голос есть → человек тут.
        Прощание — человек вышел. В чужом тексте «X ушёл» — X вышел."""
        self._expire()
        low = (speaker or "").strip().lower()
        if low and low != "unknown":
            if text and _FAREWELL_RE.search(text):
                self.mark_gone(speaker.strip())
            else:
                self._present[low] = (speaker.strip(), time.time())
        if text:
            for disp in [d for d in self.present_names() if d.lower() != low]:
                self._maybe_others_gone(text, {disp})

    def mark_present(self, name: str) -> None:
        if name and name.strip():
            self._present[name.strip().lower()] = (name.strip(), time.time())

    def mark_gone(self, name: str) -> None:
        if name and name.strip():
            self._present.pop(name.strip().lower(), None)

    def on_no_answer(self, name: str | None) -> None:
        """Нить переспросила несколько раз, ответа нет — человека нет."""
        if name:
            self.mark_gone(name)

    # ---------- запросы ----------

    def present_names(self) -> list[str]:
        self._expire()
        return [disp for _, (disp, _) in sorted(self._present.items())]

    def is_present(self, name: str) -> bool:
        return bool(name) and name.strip().lower() in self._present

    def pick_addressed(self) -> str | None:
        """Случайный присутствующий для адресной инициативы (создатель имеет приоритет)."""
        names = self.present_names()
        if not names:
            return None
        try:
            from core.config import CREATOR_NAME
            creator = [n for n in names if n.lower() == CREATOR_NAME.strip().lower()]
        except Exception:  # noqa: BLE001
            creator = []
        if creator and (len(names) == 1 or random.random() < 0.5):
            return creator[0]
        return random.choice(names)

    # ---------- внутреннее ----------

    def _maybe_others_gone(self, text: str, candidates: set[str]) -> None:
        """«Вася ушёл» / «Вася тоже ушёл» в чужой реплике — Васи нет."""
        low = text.lower()
        for disp in list(candidates):
            if re.search(_GONE_ABOUT_RE.format(n=re.escape(disp.lower())), low):
                self.mark_gone(disp)

    def _expire(self) -> None:
        if self._timeout_sec <= 0:
            return
        now = time.time()
        for key in [k for k, (_, ts) in self._present.items()
                    if now - ts > self._timeout_sec]:
            self._present.pop(key, None)
