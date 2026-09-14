"""vision — Нимфея ВИДИТ экран компьютера (v14.5).

Как это устроено (и почему без второй нейронки):
  • глаза — ТА ЖЕ gemma3:4b в Ollama: она мультимодальная, скриншот уходит
    прямо в /api/chat параметром images. Отдельный VLM на 6 ГБ не влезает
    рядом с LLM — своп моделей это десятки секунд тишины на каждый взгляд.
  • ФОНОВЫЕ НАБЛЮДЕНИЯ: когда в комнате тихо и она молчит, поток vision
    раз в VISION_INTERVAL_SEC снимает экран и просит модель описать кадр
    в 1-2 предложениях. Наблюдение живёт в трёх слоях памяти:
      - контекст: последние VISION_RECENT_N наблюдений в каждом промпте;
      - постоянная память: факт «Видела на экране: …» (memory facts, throttle);
      - RAG: факт индексируется эмбеддером → «куда я положил камень» найдётся.
  • РЕЖИМ ПРОСМОТРА (видео/игра): тишина дольше VISION_WATCH_QUIET_SEC и кадры
    меняются (детектор движения) → интервал взгляда ужимается до
    VISION_WATCH_INTERVAL_SEC — она «смотрит», как человек у телевизора.
  • НЕ МЕШАЕТ РЕЧИ: busy_check (говорит / worker пайплайна жив) — наблюдение
    откладывается; Ollama не конкурирует с ответом собеседнику.
  • ВОПРОСЫ ПРО ЭКРАН: «что на экране», «где кнопка», «куда я положил…» —
    пайплайн снимает СВЕЖИЙ кадр (describe_now, ~2-4 с) и отдаёт ей в промпт.
    Если разглядеть не вышло — модель отвечает с префиксом «ВОПРОС:», и этот
    вопрос уходит в инициативы (она сама переспросит у комнаты).
  • Зрение «минус шесть»: мелкий текст и детали часто не разобрать — персона
    честно говорит об этом (и это правда: скриншот сжат до 896px по ширине).

capture_fn инъекцируется для тестов: по умолчанию PIL.ImageGrab (Windows).
"""
from __future__ import annotations

import io
import logging
import re
import threading
import time
from collections import deque

import numpy as np

from core.config import (VISION_BRIEF, VISION_BRIEF_CHARS,
                         VISION_COMMENT_COOLDOWN_SEC, VISION_COMMENT_DELAY_SEC,
                         VISION_COMMENT_INTERRUPT_GUARD_SEC,
                         VISION_COMMENTS_ENABLED, VISION_ENABLED,
                         VISION_FACT_MIN_SEC, VISION_INTERVAL_SEC,
                         VISION_JPEG_QUALITY, VISION_MAX_WIDTH,
                         VISION_MIN_QUIET_SEC, VISION_NOVELTY_THRESHOLD,
                         VISION_OBSERVATION_TTL_SEC, VISION_QUESTION_COOLDOWN_SEC,
                         VISION_RECENT_N, VISION_SCENE_CHANGE_THRESHOLD,
                         VISION_WATCH_INTERVAL_SEC, VISION_WATCH_QUIET_SEC,
                         VISION_WINDOW_TITLE, VISION_WINDOW_TITLE_MAX)

log = logging.getLogger("vision")

# «что я делаю / что на экране / где кнопка / куда я положил камень» —
# реплики НЕ адресованы ей по имени, но про экран — она отвечает
_SCREEN_REQUEST_RE = re.compile(
    r"(?:что\s+(?:я|там|у\s+меня|происходит|на\s+(?:экране|мониторе)))"
    r"|(?:что\s+ты\s+видишь|что\s+видно)"
    r"|(?:где\s+(?:эта|это|тут|здесь|кнопк|настройк|пункт|папк|файл|окно))"
    r"|(?:куда\s+я\s+(?:положил|положила|дел|дет|поставил|поставила|закинул))"
    r"|(?:посмотри|гляни|взгляни)\b"
    r"|(?:прочитай\s+(?:что|там|это))"
    r"|(?:найди\s+на\s+экране|поищи\s+на\s+экране)"
    r"|(?:видишь\s+(?:ли\s+)?(?:что|экран|монитор))",
    re.IGNORECASE)

_OBS_SYSTEM = (
    "Опиши кадр экрана компьютера. Пиши только то, что РЕАЛЬНО видно: какое "
    "приложение/окно открыто, это игра, видео, код, чат или работа. 1-2 "
    "предложения. Без фантазий: не выдумывай ни содержимого текста, ни имён "
    "людей, ни количества окон — мелкий текст и детали не читаемы. Если важное "
    "не разобрать, задай короткий вопрос, начав сообщение ровно с «ВОПРОС: ».")
_OBS_ON_DEMAND = (
    "Глянь на экран и ответь по делу. Только то, что видно; не разобрал — "
    "честно скажи, что именно. Максимум два коротких предложения.")

# детектор движения: средняя разница кадров (0..255) выше этого — «кадры меняются»
_MOTION_THRESHOLD = 6.0
_MOTION_HISTORY = 4          # сколько последних разниц помним

# Ограничитель галлюцинаций: описание с этими маркерами неуверенности в
# постоянную память не пишем (фактом «Видела на экране: …» становились выдумки
# вроде «чаты с Юлей, Китой и Дэнкой»). В recent-контекст попадает всё равно.
_VISION_HEDGE_RE = re.compile(
    r"(?<!\w)(?:кажется|похоже|возможно|какой-то|какая-то|какие-то|кто-то|"
    r"не\s+разглядел|не\s+разобра|не\s+уверен|судя\s+по\s+всему|"
    r"видимо|похоже\s+на)(?!\w)", re.IGNORECASE)


def active_window_title(max_len: int = 90) -> str:
    """Заголовок foreground-окна (Windows, ctypes — без новых зависимостей).

    «Дота 2», «Mozilla Firefox», «Discord» — модель понимает, ВО ЧТО человек
    играет/смотрит, а не разглядывает безымянный экран. Пусто — не удалось."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = " ".join(buf.value.split())   # схлопнуть пробелы/переносы
        return title[:max_len]
    except Exception:  # noqa: BLE001 — не Windows/headless, зрению не мешает
        return ""


def is_screen_request(text: str) -> bool:
    """Реплика про экран/происходящее — даже без имени она отвечает."""
    return bool(_SCREEN_REQUEST_RE.search(text or ""))


def _motion_value(prev_gray: np.ndarray | None, gray: np.ndarray) -> float:
    """Средняя разница яркости двух уменьшенных кадров (0..255)."""
    if prev_gray is None:
        return 0.0
    if prev_gray.shape != gray.shape:
        return 255.0
    return float(np.mean(np.abs(prev_gray.astype(np.float32) - gray.astype(np.float32))))


def parse_observation(text: str) -> tuple[str, str]:
    """Ответ модели → (текст, вид): 'question' если не разглядела и спрашивает."""
    text = (text or "").strip()
    if not text:
        return "", ""
    if re.match(r"^(?:вопрос|question)\s*:", text, re.IGNORECASE):
        return text.split(":", 1)[1].strip() or text, "question"
    return text, "statement"


def _default_capture(max_width: int, quality: int) -> tuple[bytes, np.ndarray] | None:
    """Скриншот основного экрана → (jpeg, серый мини-кадр для детектора движения)."""
    try:
        from PIL import Image, ImageGrab
        img = ImageGrab.grab().convert("RGB")
    except Exception as exc:  # noqa: BLE001 — headless/нет экрана
        log.warning("[WARNING] захват экрана не удался: %s", exc)
        return None
    if img.width > max_width:
        img = img.resize((max_width, max(1, int(img.height * max_width / img.width))))
    buf = io.BytesIO()
    try:
        img.save(buf, "JPEG", quality=quality)
    except Exception:  # noqa: BLE001
        return None
    gray = np.asarray(img.resize((64, 36)).convert("L"))
    return buf.getvalue(), gray


class VisionModule:
    def __init__(self, memory, llm, *, capture_fn=None, enabled: bool = VISION_ENABLED) -> None:
        self.memory = memory
        self.llm = llm
        self.enabled = enabled
        self._capture = capture_fn or (lambda: _default_capture(VISION_MAX_WIDTH,
                                                                VISION_JPEG_QUALITY))
        self.on_observation = None      # callable(text: str, kind: str) — проводит pipeline
        self.busy_check = None          # callable() -> bool: говорит/думает — фон ждёт
        self.recent: deque[dict] = deque(maxlen=8)   # {ts, text} — контекст промпта
        self._pending_question = ""     # не разглядела — спросит через инициативу
        self._last_question_ts = 0.0    # дедуп повторных «ВОПРОС: …»
        self._last_question_text = ""
        self._motion: deque[float] = deque(maxlen=_MOTION_HISTORY)
        self._last_gray: np.ndarray | None = None
        self._last_user_ts = time.time()
        self._last_fact_ts = 0.0
        self._last_fact_text = ""
        self._last_comment_ts = 0.0
        self._last_comment_text = ""
        # последние ОЗВУЧЕННЫЕ комментарии: статичный экран LLM описывает каждый
        # раз чуть другими словами — сравнения только с последним не хватало,
        # и она комментировала один и тот же экран всю ночь
        self._recent_comment_texts: list[str] = []
        self._pending_comment: dict | None = None
        self._activity_seq = 0
        self._next_capture = 0.0
        self._watch_override: bool | None = None   # хоткей: принудительный просмотр
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_watch_override(self, value: bool | None) -> None:
        """Хоткей: None = авто (тишина+движение), True/False = принудительно."""
        self._watch_override = value
        log.info("vision: режим просмотра %s",
                 "принудительно ВКЛ" if value else
                 "принудительно ВЫКЛ" if value is False else "авто")

    # --- события извне ---
    def note_user(self) -> None:
        """Пайплайн: человек что-то сказал — комментарий ОТКЛАДЫВАЕТСЯ, а не
        убивается: дослушаем собеседника и всё равно вставим наблюдение.
        Раньше обнуляли _pending_comment — на живом диалоге комментарий умирал
        до «due» всегда (одна из причин «видит, но молчит»). TTL ограничивает
        жизнь кандидата, бесконечных откладываний нет."""
        self._last_user_ts = time.time()
        self._activity_seq += 1
        if self._pending_comment:
            self._pending_comment["due"] = max(self._pending_comment["due"],
                                               time.time() + VISION_COMMENT_DELAY_SEC)

    # --- жизнь фона ---
    def start(self) -> None:
        if not self.enabled:
            log.info("vision выключен (NIMA_VISION=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="Vision", daemon=True)
        self._thread.start()
        log.info("vision запущен: взгляд раз в %.0f с (просмотр: раз в %.0f с)",
                 VISION_INTERVAL_SEC, VISION_WATCH_INTERVAL_SEC)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(1.0)
            if self._stop.is_set():
                return
            now = time.time()
            self._dispatch_comment(now)
            if now < self._next_capture:
                continue
            if self.busy_check and self._busy_safe():
                self._next_capture = now + 3.0   # она занята — отложим, не мешаем
                continue
            if now - self._last_user_ts < VISION_MIN_QUIET_SEC:
                self._next_capture = now + 3.0
                continue
            self._observe_tick(now)

    def _busy_safe(self) -> bool:
        try:
            return bool(self.busy_check())
        except Exception:  # noqa: BLE001
            return False

    def _observe_tick(self, now: float) -> None:
        shot = self._capture()
        if not shot:
            self._next_capture = now + 10.0
            return
        jpeg, gray = shot
        motion = _motion_value(self._last_gray, gray)
        self._last_gray = gray
        self._motion.append(motion)
        watch = self._watch_mode(now)
        self._next_capture = now + (VISION_WATCH_INTERVAL_SEC if watch
                                    else VISION_INTERVAL_SEC)
        text = self.describe_frame(jpeg, _OBS_SYSTEM)
        if not text:
            return
        parsed, kind = parse_observation(text)
        if not parsed:
            return
        if kind == "question":
            # недоглядела — вопрос идёт в инициативу через on_observation ниже,
            # но ОДИН И ТОТ ЖЕ вопрос не переспрашиваем чаще кулдауна (в логах
            # «Что там написано в окне ZCode?» уходило в инициативы 12 раз
            # за 10 минут)
            if self._last_question_text \
                    and _overlap(parsed, self._last_question_text) >= 0.6 \
                    and now - self._last_question_ts < VISION_QUESTION_COOLDOWN_SEC:
                log.info("vision: вопрос про экран повторяется — молчу (кулдаун)")
                return
            self._last_question_ts = now
            self._last_question_text = parsed
            self._pending_question = parsed
            if self.on_observation:
                try:
                    self.on_observation(parsed, kind)
                except Exception:  # noqa: BLE001
                    log.exception("[ERROR] обработчик наблюдения упал")
            return
        self.remember(parsed, kind, now)
        # statement: озвучивание идёт ТОЛЬКО отложенным комментарием
        # (_pending_comment → _dispatch_comment → on_observation(..., "comment")).
        # Прямой on_observation для statement раньше вызывался впустую (pipeline
        # его игнорировал) — убран, чтобы не было двойного пути наблюдения.
        if not self._is_novel_comment(parsed, motion):
            log.debug("vision: комментарий skip=duplicate motion=%.1f", motion)
            return
        # не затираем валидный, ещё не выстреливший кандидат новым: если прошлый
        # комментарий ждёт cooldown и не протух — даём ему шанс озвучиться.
        prev = self._pending_comment
        if prev and now - prev["ts"] <= VISION_OBSERVATION_TTL_SEC \
                and _overlap(parsed, prev["text"]) >= (1.0 - VISION_NOVELTY_THRESHOLD):
            log.debug("vision: комментарий skip=pending_still_valid motion=%.1f", motion)
            return
        self._pending_comment = {"ts": now, "due": now + VISION_COMMENT_DELAY_SEC,
                                 "text": parsed, "seq": self._activity_seq}
        log.info("vision: новый комментарий запланирован через %.0f с (motion=%.1f)",
                 VISION_COMMENT_DELAY_SEC, motion)

    def _is_novel_comment(self, text: str, motion: float) -> bool:
        """Стоит ли планировать комментарий про этот кадр.

        Сцена НОВА относительно ВСЕХ недавних ОЗВУЧЕННЫХ комментариев (не только
        последнего: LLM перефразирует статичный экран, и одно-сравнение
        пропускало повторы — живой тест 2026-09-11: один и тот же редактор
        комментировался всю ночь). Первый комментарий проходит всегда; далее —
        если описание достаточно отличается от каждого из последних.
        Новизна текста имеет приоритет: одинаковую сцену не повторяем, даже если
        детектор движения показал скачок (motion меряет соседние кадры, а не
        отличие от уже сказанного).
        """
        if not VISION_COMMENTS_ENABLED:
            return False
        seen = [t for t in (self._last_comment_text, *self._recent_comment_texts) if t]
        if not seen:
            return True
        return all(_overlap(text, prev) < (1.0 - VISION_NOVELTY_THRESHOLD)
                   for prev in seen)

    def _dispatch_comment(self, now: float) -> None:
        item = self._pending_comment
        if not item or now < item["due"]:
            return
        if now - item["ts"] > VISION_OBSERVATION_TTL_SEC:
            self._pending_comment = None
            log.info("vision: комментарий skip=expired")
            return
        if now - self._last_comment_ts < VISION_COMMENT_COOLDOWN_SEC:
            return
        if self._busy_safe():
            return
        # «Не перебивать человека» держит ТОЛЬКО временной guard: если недавно
        # была реплика — ждём. От seq-равенства отказались: note_user() и так
        # обнуляет _pending_comment при активности, а жёсткая проверка seq
        # дополнительно теряла валидные комментарии на гонке потоков (это была
        # одна из причин «видит, но молчит»). seq оставлен только для лога.
        if VISION_COMMENT_INTERRUPT_GUARD_SEC > 0 and \
                now - self._last_user_ts < VISION_COMMENT_INTERRUPT_GUARD_SEC:
            return
        self._pending_comment = None
        self._last_comment_ts = now
        self._last_comment_text = item["text"]
        self._recent_comment_texts.append(item["text"])
        del self._recent_comment_texts[:-5]   # помним последние 5 озвученных
        if self.on_observation:
            try:
                self.on_observation(item["text"], "comment")
                log.info("vision: фоновый комментарий отправлен age=%.1f", now - item["ts"])
            except Exception:  # noqa: BLE001
                log.exception("[ERROR] фоновый комментарий не отправлен")

    def _watch_mode(self, now: float) -> bool:
        """Режим просмотра: тихо И кадры меняются (игра/видео)."""
        if self._watch_override is not None:
            return self._watch_override
        if now - self._last_user_ts < VISION_WATCH_QUIET_SEC:
            return False
        if len(self._motion) < _MOTION_HISTORY:
            return False
        return sum(1 for m in self._motion if m > _MOTION_THRESHOLD) >= 3

    # --- описание кадра (тот же Ollama) ---
    def describe_frame(self, jpeg: bytes, system: str = _OBS_ON_DEMAND,
                       window: str | None = None) -> str:
        ask = "Что на экране?"
        if window is None and VISION_WINDOW_TITLE:
            window = active_window_title(VISION_WINDOW_TITLE_MAX)
        if window:
            ask = f"Активное окно: «{window}». Что происходит на экране?"
        text = self.llm.generate_vision(system, ask, jpeg) or ""
        return text.strip()

    def describe_now(self) -> str:
        """Свежий взгляд по запросу (~2-4 с: захват <0.3 с + короткая генерация).
        Вызывается из пайплайна, когда реплика про экран."""
        if not self.enabled:
            return ""
        shot = self._capture()
        if not shot:
            return ""
        jpeg, _gray = shot
        # НЕ трогаем self._last_gray: on-demand кадр снимается между фоновыми
        # взглядами, и перезапись эталона ломала бы motion-детектор фона
        # (ложный скачок/ноль на следующем фоновом тике → неверный watch-режим).
        text = self.describe_frame(jpeg)
        parsed, kind = parse_observation(text)
        if parsed:
            self.remember(parsed, kind)
        return text

    # --- память увиденного ---
    def remember(self, text: str, kind: str, now: float | None = None) -> None:
        """Контекст (промпт) — всегда; постоянная память (факт → RAG) — с троттлингом.

        В recent кладём и полный text (для инициатив/свежести), и краткую метку
        brief (для промпта — экономит токены, см. docs/LATENCY_OPTIMIZATION.md).
        Полное описание не теряется: ниже уходит фактом «Видела на экране: …».
        """
        now = now or time.time()
        self.recent.append({"ts": now, "text": text, "brief": _brief(text)})
        if kind == "question":
            return   # недоглядевшее — не факт, это вопрос
        if _VISION_HEDGE_RE.search(text):
            log.info("vision: неуверенное описание в память не пишу: %r", text[:60])
            return
        if now - self._last_fact_ts < VISION_FACT_MIN_SEC:
            return
        if _overlap(text, self._last_fact_text) >= 0.6:
            return   # та же сцена — память не засоряем
        self._last_fact_ts = now
        self._last_fact_text = text
        try:
            self.memory.append_fact(f"Видела на экране: {text}",
                                    source="vision", tags=["vision", "screen"])
        except Exception:  # noqa: BLE001
            log.exception("[ERROR] факт о кадре не записан")

    def pending_question(self) -> str:
        q = self._pending_question
        self._pending_question = ""
        return q

    def fresh_observation(self, max_age_sec: float = 90.0) -> str:
        """Последнее наблюдение, если оно ещё свежее (для инициатив про экран)."""
        if not self.recent:
            return ""
        last = self.recent[-1]
        return last["text"] if time.time() - last["ts"] <= max_age_sec else ""

    def recent_summary(self, limit: int = VISION_RECENT_N) -> str:
        """Блок для промпта: что она видела в последние минуты.

        При VISION_BRIEF в промпт идёт КРАТКАЯ метка (it['brief']) — экономит
        токены prompt-eval (латентность). Полное описание не теряется: оно в
        постоянной памяти фактом «Видела на экране: …» (RAG найдёт по запросу),
        а свежий взгляд по вопросу про экран даёт describe_now.
        """
        items = list(self.recent)[-limit:]
        if not items:
            return ""
        if VISION_BRIEF:
            return "\n".join(f"- {it.get('brief') or _brief(it['text'])}" for it in items)
        return "\n".join(f"- {it['text']}" for it in items)


def _brief(text: str) -> str:
    """Краткая метка наблюдения для промпта: первое предложение, до
    VISION_BRIEF_CHARS символов. Полный текст остаётся в памяти/фактах."""
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    # первое предложение (до . ! ? …) — обычно самая суть кадра
    m = re.split(r"(?<=[.!?…])\s", text, maxsplit=1)
    head = m[0] if m else text
    if len(head) > VISION_BRIEF_CHARS:
        head = head[:VISION_BRIEF_CHARS].rstrip() + "…"
    return head


def _overlap(a: str, b: str) -> float:
    """Доля общих значимых токенов (анти-повтор фактов про одну сцену)."""
    try:
        from memory.memory_module import MemoryModule
        ta, tb = MemoryModule.normalize_text(a), MemoryModule.normalize_text(b)
    except Exception:  # noqa: BLE001 — тестовая память
        ta = {w for w in re.findall(r"\w+", a.lower()) if len(w) >= 3}
        tb = {w for w in re.findall(r"\w+", b.lower()) if len(w) >= 3}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))
