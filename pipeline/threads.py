"""threads — нити разговора (требование 19): Нимфея как живой человек не
забывает разговор сразу после ответа.

Хвосты (tails):
  • question — она задала вопрос и ждёт ответа: нет ответа THREADS_FIRST_SEC
    → переспросить; ещё THREADS_SECOND_SEC → переспросить с претензией;
  • statement — она что-то сказала, реакции нет → повторить с претензией
    (один раз).

Watchdog раз в 5 с проверяет открытые хвосты; генерацию переспроса делает
пайплайн (callback make_follow_up(tail)), он же пишет хвосты. Хвост живёт
максимум 2 подхвата, после — закрыт («не выводи из себя человека»).
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from core.config import THREADS_ENABLED, THREADS_FIRST_SEC, THREADS_SECOND_SEC

log = logging.getLogger("threads")


@dataclass
class Tail:
    kind: str                     # 'question' | 'statement'
    text: str                     # что сказала (вопрос/реплика)
    speaker: str                  # к кому обращалась
    channel: str
    ts: float = field(default_factory=time.time)
    nudges: int = 0               # сколько раз уже подхватила
    deadline: float = 0.0

    def __post_init__(self) -> None:
        self.deadline = self.ts + THREADS_FIRST_SEC


class ConversationThreads:
    def __init__(self) -> None:
        self.enabled = THREADS_ENABLED
        self.make_follow_up = None      # callable(tail: Tail) — генерирует и говорит переспрос
        self.on_tail_dropped = None    # callable(speaker) — хвост закрыт без ответа
        self._tails: list[Tail] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.enabled:
            return
        threading.Thread(target=self._loop, name="Threads", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # --- события из пайплайна ---
    def on_bot_reply(self, text: str, speaker: str, channel: str, *, is_question: bool) -> None:
        """После каждой содержательной реплики Нимфеи."""
        if not self.enabled:
            return
        kind = "question" if is_question else "statement"
        tail = Tail(kind=kind, text=text[-200:], speaker=speaker, channel=channel)
        with self._lock:
            self._tails.append(tail)
            del self._tails[:-4]  # не более 4 открытых нитей

    def on_user_reply(self, text: str) -> None:
        """Любая реплика человека закрывает открытые хвосты: ответ получен."""
        with self._lock:
            self._tails.clear()

    def pending_question_for(self, speaker: str | None) -> bool:
        """Открытый вопрос-хвост, ждущий ответа именно от этого голоса."""
        low = (speaker or "").strip().lower()
        if not low:
            return False
        with self._lock:
            return any(t.kind == "question" and (t.speaker or "").strip().lower() == low
                       for t in self._tails)

    def pending_summary(self) -> str:
        """Для промпта: чего она ждёт (чтобы не путала контекст)."""
        with self._lock:
            tails = list(self._tails)
        if not tails:
            return ""
        lines = [f"- ждёт ответа на ({tail.kind}): {tail.text[:120]}" for tail in tails]
        return "Открытые хвосты разговора:\n" + "\n".join(lines)

    # --- watchdog ---
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(5)
            if self._stop.is_set():
                return
            now = time.time()
            due: list[Tail] = []
            with self._lock:
                alive: list[Tail] = []
                for tail in self._tails:
                    if now < tail.deadline:
                        alive.append(tail)
                        continue
                    tail.nudges += 1
                    if tail.nudges >= 3:
                        # хвост закрыт — хватит приставаться; молчание = человека нет
                        if self.on_tail_dropped and tail.speaker:
                            try:
                                self.on_tail_dropped(tail.speaker)
                            except Exception:  # noqa: BLE001
                                pass
                        continue
                    tail.ts = now
                    tail.deadline = now + THREADS_SECOND_SEC
                    alive.append(tail)
                    due.append(tail)
                self._tails = alive
            for tail in due:
                log.info("threads: хвост %s #%d (ждёт %s)",
                         tail.kind, tail.nudges, tail.speaker or "всех")
                try:
                    if self.make_follow_up:
                        self.make_follow_up(tail)
                except Exception:  # noqa: BLE001
                    log.exception("[ERROR] переспрос упал")


def is_question(text: str) -> bool:
    """Грубая эвристика: реплика-вопрос?"""
    if "?" in text:
        return True
    starters = ("скажи", "расскажи", "как ты", "что ты", "почему", "зачем", "когда",
                "где", "кто", "какой", "какая", "сколько", "а ты", "правда")
    low = text.lower()
    return low.startswith(starters) and len(low) > 8
