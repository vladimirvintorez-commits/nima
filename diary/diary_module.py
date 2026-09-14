"""diary — дневник дня (v14.7): память переживает не только факты, но и дни.

Раз в сутки (после DIARY_HOUR ночи) модуль берёт вчерашние реплики диалога и
факты из memory.json и просит LLM сжать день в короткое воспоминание от
первого лица. Итог записывается ФАКТОМ «Дневник за YYYY-MM-DD: …» — он
попадает в контекст, в RAG и остаётся навсегда (memory.json никогда не
пересоздаётся). Через неделю у неё связное «а вчера мы…», а не обрывки.

Модуль изолирован: знает про память (методы query/append_fact) и LLM
(метод generate) — больше ни про что.
"""
from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta

from core.config import (DIARY_CHECK_SEC, DIARY_ENABLED, DIARY_HOUR,
                         DIARY_LAST_PATH, DIARY_MAX_EPISODES)

log = logging.getLogger("diary")

_SYSTEM = (
    "Ты — Нимфея, живая девушка-лиса. Тебе на вход дают записи твоего вчерашнего "
    "дня (разговоры, факты, что видела на экране). Сожми день в 3-6 предложений "
    "ВОСПОМИНАНИЙ от первого лица, своим тоном: что делали, во что играли/смотрели, "
    "что обсуждали, что запомнилось. Без списков, без дат, без слов «записи» и "
    "«лог» — просто как человек вспоминает вечер. Если день был почти пуст — "
    "одно-два предложения, честно.")
_DIAG_SYSTEM = (
    "Ты — Нимфея. Это ЗАПИСИ твоего вчерашнего дня. Верни только текст дневника.")


def diary_date_now(now: datetime | None = None) -> date:
    """За какой день пишем дневник: после DIARY_HOUR — вчерашний, до — позавчерашний.

    Ночь 00:00–02:59 человек ещё не «закончил» текущий день, но и предыдущий
    дневник ещё не написан — пишем его за позавчера (не терять день).
    """
    now = now or datetime.now()
    if now.hour >= DIARY_HOUR:
        return (now - timedelta(days=1)).date()
    return (now - timedelta(days=2)).date()


class DiaryModule:
    def __init__(self, memory, llm, *, enabled: bool = DIARY_ENABLED,
                 last_path=DIARY_LAST_PATH) -> None:
        self.memory = memory
        self.llm = llm
        self.enabled = enabled
        self.last_path = last_path
        self.last_summary = ""           # последний дневник (debug menu)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            log.info("diary выключен (NIMA_DIARY=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="Diary", daemon=True)
        self._thread.start()
        log.info("diary запущен (проверка раз в %.0f мин, запись после %02d:00)",
                 DIARY_CHECK_SEC / 60, DIARY_HOUR)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(DIARY_CHECK_SEC)
            if self._stop.is_set():
                return
            try:
                self.check()
            except Exception:  # noqa: BLE001 — дневник не должен ронять что-либо
                log.exception("[ERROR] дневник не записан")

    def _last_written(self) -> str:
        try:
            return self.last_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def check(self, now: datetime | None = None) -> str:
        """Если дневник за нужный день ещё не написан — написать. Должно быть
        идемпотентным: повторный вызов в ту же ночь ничего не делает."""
        day = diary_date_now(now)
        day_str = day.isoformat()
        if self._last_written() >= day_str:
            return ""
        entries = self.memory.query(since=day_str, until=day_str,
                                    limit=DIARY_MAX_EPISODES)
        facts = [f for f in self._facts_of(day_str)]
        if not entries and not facts:
            self._mark(day_str)     # день пуст — не пытаться снова каждую проверку
            return ""
        diary = self._summarize(entries, facts)
        if not diary:
            return ""               # LLM не ответила — попробуем на следующей проверке
        text = f"Дневник за {day_str}: {diary}"
        self.memory.append_fact(text, source="diary", tags=["diary", "memory"])
        self._mark(day_str)
        self.last_summary = diary
        log.info("diary: записан дневник за %s (%d зн.)", day_str, len(diary))
        return text

    def _facts_of(self, day_str: str) -> list[str]:
        try:
            facts = self.memory._active_facts()
        except Exception:  # noqa: BLE001 — тестовая память
            return []
        return [str(f.get("text", "")).strip() for f in facts
                if str(f.get("ts", ""))[:10] == day_str and str(f.get("text", "")).strip()]

    def _summarize(self, entries: list[dict], facts: list[str]) -> str:
        lines = [f"- ({e.get('role', 'user')}) {e['content']}" for e in entries]
        lines += [f"- [факт] {t}" for t in facts[:15]]
        if not lines:
            return ""
        user_text = "Записи дня:\n" + "\n".join(lines[:DIARY_MAX_EPISODES + 15])
        return (self.llm.generate(_SYSTEM, [], user_text) or "").strip()

    def _mark(self, day_str: str) -> None:
        try:
            self.last_path.parent.mkdir(parents=True, exist_ok=True)
            self.last_path.write_text(day_str, encoding="utf-8")
        except OSError as exc:
            log.warning("[WARNING] не отметить дневник записанным: %s", exc)
