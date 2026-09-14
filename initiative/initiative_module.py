"""Stream-friendly инициатива Нимфеи.

Таймеры разделены по смыслу: валидная речь пользователя задаёт основную
тишину, чат — короткий anti-interrupt gate, обычная реплика Нимы — только
короткую паузу, а proactive имеет собственный cooldown. Шум/эхо должны быть
отфильтрованы пайплайном до вызова note_activity().
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass

from core.config import (INITIATIVE_CHAT_QUIET_SEC, INITIATIVE_ENABLED,
                         INITIATIVE_INTERRUPT_GUARD_SEC, INITIATIVE_LIVE_WEB,
                         INITIATIVE_MIN_GAP_SEC, INITIATIVE_POLL_SEC,
                         INITIATIVE_REPLY_QUIET_SEC, INITIATIVE_SILENCE_SEC,
                         INITIATIVE_STREAM_PROFILE, INITIATIVE_TOPIC_COOLDOWN_SEC)

log = logging.getLogger("initiative")


def _text_overlap(a: str, b: str) -> float:
    """Доля общих слов (0..1) — анти-повтор при разгоне темы."""
    wa = {w.lower() for w in str(a).split() if len(w) > 2}
    wb = {w.lower() for w in str(b).split() if len(w) > 2}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def _trim_to_sentence(text: str, limit: int) -> str:
    """Эпизод памяти для темы — целыми предложениями (урок 14.09: срез [:200]
    резал слова пополам, и в «Тему» уходили огрызки вроде «…верн»)."""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in (". ", "! ", "? ", "… "):
        pos = cut.rfind(sep)
        if pos > 40:
            return cut[:pos + 1].strip()
    return cut.rsplit(" ", 1)[0].strip()

_RANDOM_QUESTIONS = [
    "какая у тебя сейчас песня застряла в голове",
    "какое самое странное сновидение ты помнишь",
    "если бы можно было завести любое животное, кого бы взял",
    "какую еду мог бы есть каждый день и не надоедало",
    "что тебя рассмешило за последний день",
    "какой фильм или сериал стоило бы посмотреть втроём с друзьями",
    "где бы ты хотел оказаться прямо сейчас",
    "какой у тебя самый бесполезный, но любимый навык",
    "если бы у тебя был свободный день без дел — как бы ты его провёл",
    "какое место из детства помнишь лучше всего",
    "какая мелочь может испортить тебе настроение мгновенно",
    "о чём ты думал сегодня, когда отвлекался от дел",
    "какая книга или игра остались в голове надолго",
    "если бы ты мог что-то поменять в своей комнате — что именно",
    "какой совет тебе когда-то дали, а он оказался бесполезным",
    "что ты умеешь делать руками и гордишься этим",
    "какой самый странный комплимент ты получал",
    "если бы ты мог ужин с любым человеком — кого бы позвал",
    "какая привычка у тебя есть, о которой мало кто догадывается",
    "что тебя недавно удивило в обычном деле",
    "какой звук для тебя самый приятный",
    "если бы ты на месяц уехал куда угодно — куда и почему",
    "какую вещь ты купил и ни разу не пожалел",
    "что ты считаешь переоценённым, а все вокруг восхищаются",
    "какое у тебя самое тёплое воспоминание про друзей",
    "если бы ты мог вернуть одну вещь из прошлого — что бы вернул",
    "какое занятие тебя полностью поглощает, когда начинаешь",
    "что бы ты хотел уметь, но так и не нашёл времени научиться",
    "какой самый смешной случай с тобой случался на улице",
    "о чём тебе могли бы часами рассказывать, и ты бы слушал",
]


@dataclass(frozen=True)
class Eligibility:
    allowed: bool
    reason: str
    user_quiet: float
    chat_quiet: float
    reply_quiet: float
    proactive_gap: float


class InitiativeModule:
    def __init__(self, memory, web=None, *, clock=None) -> None:
        self.memory = memory
        self.web = web
        self.enabled = INITIATIVE_ENABLED
        self.on_topic = None            # callable(source: str, topic: str)
        self.busy_check = None          # callable() -> bool: pipeline/TTS заняты
        self._clock = clock or time.monotonic
        now = self._clock()
        self._last_user_ts = now
        self._last_chat_ts = 0.0
        self._last_nima_reply_ts = 0.0
        self._last_proactive_ts = 0.0
        self._activity_seq = 0
        # темы, уже озвученные инициативой: (нормализованный текст, ts) —
        # защита от «часами говорит одно и то же»
        self._recent_topics: list[tuple[str, float]] = []
        # активная тема (разгон тем): она не бросает тему после одной реплики,
        # а развивает её 1-2 хода (новая деталь из памяти), пока человек молчит
        self._active_topic = ""
        self._topic_depth = 0
        self._topic_ts = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending_facts: list[str] = []

    # --- жизненный цикл ---
    def start(self) -> None:
        if not self.enabled:
            log.info("initiative выключен (NIMA_INITIATIVE=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="Initiative", daemon=True)
        self._thread.start()
        log.info("initiative запущен (profile=%s, user=%.0f с, gap=%.0f с, "
                 "chat=%.0f с, reply=%.0f с)",
                 "stream" if INITIATIVE_STREAM_PROFILE else "safe",
                 INITIATIVE_SILENCE_SEC, INITIATIVE_MIN_GAP_SEC,
                 INITIATIVE_CHAT_QUIET_SEC, INITIATIVE_REPLY_QUIET_SEC)

    def stop(self) -> None:
        self._stop.set()

    # --- события извне ---
    def note_activity(self, kind: str = "user") -> None:
        """Записать уже валидированную активность.

        user/voice/manual/donation сбрасывают основную тишину; twitch/chat лишь
        ставят короткий gate. Шум и эхо сюда не должны попадать.
        """
        now = self._clock()
        with self._lock:
            if kind in ("chat", "twitch"):
                self._last_chat_ts = now
            else:
                self._last_user_ts = now
                # человек присоединился — тема теперь живёт в обычном диалоге,
                # инициативе её развивать больше не нужно
                self._active_topic = ""
                self._topic_depth = 0
            self._activity_seq += 1

    def note_user_activity(self) -> None:
        """Совместимость со старым API: валидная активность пользователя."""
        self.note_activity("user")

    def note_spoke(self, proactive: bool = False) -> None:
        """Обычный ответ Нимы даёт короткую паузу; proactive запускает GAP."""
        now = self._clock()
        with self._lock:
            self._last_nima_reply_ts = now
            if proactive:
                self._last_proactive_ts = now

    def add_fact(self, text: str) -> None:
        """Отложенный факт из веб-ответов — кандидат для будущей инициативы."""
        text = (text or "").strip()
        if text:
            with self._lock:
                self._pending_facts.append(text[:200])
                del self._pending_facts[:-20]

    def eligibility(self, now: float | None = None) -> Eligibility:
        """Чистая детерминированная проверка таймеров для тестов и логов."""
        now = self._clock() if now is None else now
        with self._lock:
            user_quiet = now - self._last_user_ts
            chat_quiet = float("inf") if not self._last_chat_ts else now - self._last_chat_ts
            reply_quiet = (float("inf") if not self._last_nima_reply_ts
                           else now - self._last_nima_reply_ts)
            proactive_gap = (float("inf") if not self._last_proactive_ts
                              else now - self._last_proactive_ts)
        checks = (
            (user_quiet >= INITIATIVE_SILENCE_SEC, "user_quiet"),
            (chat_quiet >= INITIATIVE_CHAT_QUIET_SEC, "chat_active"),
            (reply_quiet >= INITIATIVE_REPLY_QUIET_SEC, "nima_reply"),
            (proactive_gap >= INITIATIVE_MIN_GAP_SEC, "proactive_gap"),
        )
        reason = next((reason for ok, reason in checks if not ok), "ready")
        return Eligibility(reason == "ready", reason, user_quiet, chat_quiet,
                           reply_quiet, proactive_gap)

    def _busy_safe(self) -> bool:
        try:
            return bool(self.busy_check and self.busy_check())
        except Exception:  # noqa: BLE001
            return True

    # --- планировщик ---
    def _loop(self) -> None:
        last_reason = ""
        while not self._stop.is_set():
            self._stop.wait(INITIATIVE_POLL_SEC)
            if self._stop.is_set():
                return
            state = self.eligibility()
            reason = "busy" if state.allowed and self._busy_safe() else state.reason
            if reason != "ready":
                if reason != last_reason:
                    log.debug("initiative skip=%s user=%.1f chat=%.1f reply=%.1f gap=%.1f",
                              reason, state.user_quiet, state.chat_quiet,
                              state.reply_quiet, state.proactive_gap)
                    last_reason = reason
                continue
            with self._lock:
                seq = self._activity_seq
            topic = self._pick_topic()
            if topic is None:
                continue
            # Topic retrieval может занять время. Любая новая активность или busy
            # после него отменяет callback — не перебиваем начавшийся разговор.
            with self._lock:
                changed = seq != self._activity_seq
            if changed or not self.eligibility().allowed or self._busy_safe():
                log.info("initiative: отменена перед callback (activity/busy)")
                continue
            if INITIATIVE_INTERRUPT_GUARD_SEC > 0:
                if self._stop.wait(INITIATIVE_INTERRUPT_GUARD_SEC):
                    return
                with self._lock:
                    changed = seq != self._activity_seq
                if changed or not self.eligibility().allowed or self._busy_safe():
                    log.info("initiative: отменена guard-окном")
                    continue
            source, content = topic
            self._note_topic(content)
            self.note_spoke(proactive=True)  # резервируем cooldown до callback
            last_reason = "proactive_gap"
            log.info("initiative: %s → %r", source, content[:60])
            try:
                if self.on_topic:
                    self.on_topic(source, content)
            except Exception:  # noqa: BLE001
                log.exception("[ERROR] инициатива упала")

    # --- выбор темы ---
    @staticmethod
    def _norm_topic(text: str) -> str:
        return " ".join(sorted(set(word.lower() for word in text.split())))

    def _topic_used_recently(self, text: str, now: float) -> bool:
        """Тема уже звучала за последние INITIATIVE_TOPIC_COOLDOWN_SEC?"""
        low = set(text.lower().split())
        if not low:
            return True
        fresh = [(t, ts) for t, ts in self._recent_topics
                 if now - ts < INITIATIVE_TOPIC_COOLDOWN_SEC]
        self._recent_topics[:] = fresh[-64:]
        for other, _ts in fresh:
            other_set = set(other.split())
            if not other_set:
                continue
            shared = len(low & other_set)
            if shared / max(1, len(low | other_set)) >= 0.6:
                return True
        return False

    def _note_topic(self, text: str) -> None:
        self._recent_topics.append((self._norm_topic(text), self._clock()))

    def _pick_topic(self) -> tuple[str, str] | None:
        now = self._clock()
        seed = random.Random()
        sources: list[tuple[str, str]] = []

        # === РАЗГОН ТЕМ: активная тема не брошена и не исчерпана ===
        if self._active_topic and self._topic_depth < 2 \
                and now - self._topic_ts < 600:
            developed = self._develop_active_topic()
            if developed:
                self._topic_depth += 1
                self._topic_ts = now
                log.info("initiative: развиваю активную тему (ход %d)",
                         self._topic_depth)
                return ("develop", developed)
            # развить нечем — тема исчерпана раньше лимита
            self._active_topic = ""
            self._topic_depth = 0

        recent = self.memory.query(limit=6)
        # СВОИ записи (инициативы/хвосты) — не «слова человека»: без фильтра она
        # скармливала поиску собственные реплики («Ага, вижу… интересные факты»)
        recent = [item for item in recent
                  if item.get("speaker") not in ("initiative", "threads")]
        recent_text = " ".join(item["content"] for item in recent if item["role"] == "user")
        if recent_text:
            episodes = self.memory.recall_dialog(recent_text[:200], limit=3)
            for episode in episodes:
                sources.append(("memory", _trim_to_sentence(episode["content"], 200)))
        else:
            # Она одна: свежих реплик человека нет. Не крутим 8 вопросов по
            # кругу — тянем СЛУЧАЙНЫЙ старый разговор человека как тему
            # (ротация: каждый раз другой кусок истории).
            seed = random.Random(int(now))
            older = self.memory.query(limit=40)
            human = [item for item in older
                     if item["role"] == "user"
                     and item.get("speaker") not in ("initiative", "threads")
                     and len(item["content"]) > 15]
            if human:
                pick = seed.choice(human)
                for episode in self.memory.recall_dialog(pick["content"][:200], limit=2):
                    sources.append(("memory", _trim_to_sentence(episode["content"], 200)))

        with self._lock:
            pending = self._pending_facts.pop(0) if self._pending_facts else ""
        if pending:
            sources.append(("web", pending))

        base = recent_text[-120:] if recent_text else ""
        if INITIATIVE_LIVE_WEB and self.web and base and seed.random() < 0.4:
            hits = self.web.search(f"{base} интересные факты", limit=3)
            for hit in hits:
                if hit.get("snippet"):
                    sources.append(("web", f"{hit['title']}. {hit['snippet']}"[:200]))

        for question in _RANDOM_QUESTIONS:
            sources.append(("question", question))

        # темы, звучавшие недавно (кулдаун INITIATIVE_TOPIC_COOLDOWN_SEC),
        # отбрасываем — «одно и то же часами» больше не варианта
        sources = [(src, text) for src, text in sources
                   if not self._topic_used_recently(text, now)]
        if not sources:
            return None
        source, content = seed.choice(sources)
        # новая тема становится активной: следующие 1-2 инициативы будут
        # развивать её, а не прыгать на случайную (разгон тем)
        self._active_topic = content
        self._topic_depth = 0
        self._topic_ts = now
        return (source, content)

    def _develop_active_topic(self) -> str:
        """Следующий ход по активной теме: другой эпизод памяти про неё —
        новая деталь/история, а не повтор сказанного. Пусто — развить нечем."""
        topic = self._active_topic
        try:
            episodes = self.memory.recall_dialog(topic[:200], limit=4)
        except Exception:  # noqa: BLE001
            return ""
        for episode in episodes:
            text = str(episode.get("content", "")).strip()
            if len(text) < 40:
                continue
            # эпизод должен отличаться и от темы, и от уже звучавшего
            if _text_overlap(text, topic) >= 0.7:
                continue
            if self._topic_used_recently(text, self._clock()):
                continue
            return _trim_to_sentence(text, 200)
        return ""
