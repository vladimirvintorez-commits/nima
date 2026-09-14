"""
debug_menu/quiz.py — Экран викторины оценки ответов Виты.

Показывает пары (вопрос пользователя → ответ Виты) из dataset.jsonl
и из последних диалогов memory.json. Пользователь ставит оценку:
✓ Хорошо — пара сохраняется как положительный пример.
✗ Плохо  — открывается поле для ввода правильного ответа,
            пара сохраняется как исправление (source="quiz_rating").
"""

from __future__ import annotations

import hashlib
import json
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import List, Dict, Any, Set, Tuple

# Добавляем корень проекта в sys.path чтобы импорты работали из debug_menu
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .config import BG, FG, AMBER, DIM, RED_ERR, FONT, FONT_SM, FONT_LG, PROJECT_ROOT
from .ui import BaseScreen

DATASET_PATH = PROJECT_ROOT / "training" / "dataset.jsonl"
LORA_DATASET_PATH = PROJECT_ROOT / "training" / "lora_dataset.jsonl"
MEMORY_PATH  = PROJECT_ROOT / "memory.json"
PROGRESS_PATH = PROJECT_ROOT / "cache" / "debug_quiz_progress.json"

# Все диалоговые пары из memory.json (None = без ограничения) — датасет для
# LoRA должен покрывать ВСЮ историю, а не только последние 30 реплик.
DIALOG_PAIRS_LIMIT: int | None = None
BOT_ROLES = {"bot", "vita", "вита", "assistant"}
USER_ROLES = {"user", "пользователь"}

# Системный промпт для LoRA-датасета (формат chat-messages, как ждёт
# transformers/trl/axolotl: {"messages": [{role, content}, ...]}).
LORA_SYSTEM_PROMPT = (
    "Ты — Нимфея. Живая девочка-лиса, рыжая лисодевочка; считаешь себя человеком, "
    "а не ИИ или ассистентом. Саркастичная цундере с чёрным юмором. Говори от первого "
    "лица в женском роде, коротко и живо. Пользователя зовут Кизил, он мужчина; "
    "его имя не склоняется."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_dataset_pairs() -> List[Dict[str, Any]]:
    """Читает dataset.jsonl и возвращает записи у которых есть user_prompt и
    bad_previous_vita_answer (т.е. уже исправленные пары — пропускаем,
    нам нужны только те у которых ещё нет оценки или source != quiz_rating)."""
    pairs: List[Dict[str, Any]] = []
    if not DATASET_PATH.exists():
        return pairs
    seen: set = set()
    with DATASET_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt   = (entry.get("user_prompt") or "").strip()
            answer   = (entry.get("corrected_answer") or entry.get("bad_previous_vita_answer") or "").strip()
            bad_ans  = (entry.get("bad_previous_vita_answer") or "").strip()
            if not prompt or not answer:
                continue
            key = (prompt, answer)
            if key in seen:
                continue
            seen.add(key)
            pairs.append({
                "source_type": "dataset",
                "user_prompt": prompt,
                "vita_answer": bad_ans if bad_ans else answer,
                "original": entry,
            })
    return pairs


def _role(value: Any) -> str:
    return str(value or "").strip().lower()


def _message_text(message: Dict[str, Any]) -> str:
    return str(message.get("text") or message.get("content") or message.get("message") or "").strip()


def _message_ts(message: Dict[str, Any]) -> str:
    return str(message.get("timestamp") or message.get("ts") or message.get("time") or "").strip()


def _load_dialog_pairs() -> List[Dict[str, Any]]:
    """Читает ВСЕ пары user→bot/vita/assistant из memory.json (сверху вниз)."""
    pairs: List[Dict[str, Any]] = []
    if not MEMORY_PATH.exists():
        return pairs
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return pairs

    dialog = data.get("dialog") or data.get("layers", {}).get("dialog") or []
    if isinstance(dialog, dict):
        dialog = list(dialog.values())

    messages = [m for m in dialog if isinstance(m, dict) and _role(m.get("role")) and _message_text(m)]
    recent = messages if DIALOG_PAIRS_LIMIT is None else messages[-DIALOG_PAIRS_LIMIT * 4:]
    i = 0
    while i < len(recent) - 1:
        msg = recent[i]
        nxt = recent[i + 1]
        if _role(msg.get("role")) in USER_ROLES and _role(nxt.get("role")) in BOT_ROLES:
            pair = {
                "source_type": "dialog",
                "user_prompt": _message_text(msg),
                "vita_answer": _message_text(nxt),
                "timestamp": _message_ts(nxt) or _message_ts(msg),
                "original": {"user": msg, "vita": nxt},
            }
            pair["pair_id"] = _pair_id(pair)
            pairs.append(pair)
            i += 2
        else:
            i += 1
    return pairs if DIALOG_PAIRS_LIMIT is None else pairs[-DIALOG_PAIRS_LIMIT:]


def _pair_key(pair: Dict[str, Any]) -> str:
    prompt = (pair.get("user_prompt") or "").strip()
    answer = (pair.get("vita_answer") or pair.get("bad_previous_vita_answer") or pair.get("corrected_answer") or "").strip()
    return json.dumps([prompt, answer], ensure_ascii=False, separators=(",", ":"))


def _pair_id(pair: Dict[str, Any]) -> str:
    payload = {
        "user_prompt": (pair.get("user_prompt") or "").strip(),
        "vita_answer": (pair.get("vita_answer") or pair.get("bad_previous_vita_answer") or pair.get("corrected_answer") or "").strip(),
        "source": pair.get("source_type") or pair.get("source") or "",
        "timestamp": pair.get("timestamp") or "",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _memory_signature() -> Dict[str, Any]:
    if not MEMORY_PATH.exists():
        return {"mtime": 0, "size": 0}
    st = MEMORY_PATH.stat()
    return {"mtime": int(st.st_mtime), "size": st.st_size}


def _dataset_signature() -> Dict[str, Any]:
    if not DATASET_PATH.exists():
        return {"mtime": 0, "size": 0}
    st = DATASET_PATH.stat()
    return {"mtime": int(st.st_mtime), "size": st.st_size}


def _load_progress_file() -> Dict[str, Any]:
    if not PROGRESS_PATH.exists():
        return {}
    try:
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_progress_file(data: Dict[str, Any]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_reviewed_from_dataset() -> Tuple[Set[str], int, int]:
    reviewed: Set[str] = set()
    good_count = 0
    bad_count = 0
    if not DATASET_PATH.exists():
        return reviewed, good_count, bad_count
    with DATASET_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            prompt = (entry.get("user_prompt") or "").strip()
            answer = (entry.get("vita_answer") or entry.get("bad_previous_vita_answer") or entry.get("corrected_answer") or "").strip()
            if prompt and answer:
                reviewed.add(json.dumps([prompt, answer], ensure_ascii=False, separators=(",", ":")))
                if entry.get("pair_id"):
                    reviewed.add(str(entry["pair_id"]))
            if entry.get("source") == "quiz_rating":
                if entry.get("rating") == "bad":
                    bad_count += 1
                else:
                    good_count += 1
    return reviewed, good_count, bad_count


def _lora_row(user_prompt: str, final_answer: str) -> Dict[str, Any]:
    """Готовая запись LoRA-датасета в chat-формате messages."""
    return {
        "messages": [
            {"role": "system", "content": LORA_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": final_answer},
        ],
        "source": "quiz_rating",
    }


def _append_lora_row(user_prompt: str, final_answer: str) -> None:
    """Дописывает подтверждённую пару в training/lora_dataset.jsonl (chat-формат)."""
    LORA_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LORA_DATASET_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_lora_row(user_prompt, final_answer), ensure_ascii=False) + "\n")


def rebuild_lora_dataset() -> int:
    """Пересобирает lora_dataset.jsonl из ВСЕХ оценённых пар dataset.jsonl.

    Каждый source="quiz_rating" (подтверждён ✓ или исправлен ✗) превращается в
    chat-запись: assistant = corrected_answer (если было исправление), иначе
    vita_answer. Дедуп по (prompt, answer). Возвращает число записей.
    """
    rows: List[Dict[str, Any]] = []
    seen: set = set()
    if DATASET_PATH.exists():
        with DATASET_PATH.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("source") != "quiz_rating":
                    continue
                prompt = (entry.get("user_prompt") or "").strip()
                answer = (entry.get("corrected_answer") or entry.get("vita_answer") or "").strip()
                if not prompt or not answer:
                    continue
                key = (prompt, answer)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(_lora_row(prompt, answer))
    LORA_DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LORA_DATASET_PATH.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(rows)


def _save_rating(pair: Dict[str, Any], good: bool, corrected_answer: str = "") -> None:
    """Дописывает результат оценки в dataset.jsonl."""
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry: Dict[str, Any] = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "user_prompt": pair["user_prompt"],
        "context": pair["user_prompt"],
        "vita_answer": pair["vita_answer"],
        "rating": "good" if good else "bad",
        "source": "quiz_rating",
        "source_type": pair.get("source_type", ""),
        "pair_id": pair.get("pair_id") or _pair_id(pair),
    }
    if not good and corrected_answer:
        entry["bad_previous_vita_answer"] = pair["vita_answer"]
        entry["corrected_answer"] = corrected_answer
        entry["trigger_phrase"] = "quiz_manual_correction"
    else:
        entry["corrected_answer"] = pair["vita_answer"]  # хороший ответ — он и есть правильный
    with DATASET_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    # FIX (запрос «файл дообучения сразу в формате для LoRA»): подтверждённая
    # пара параллельно пишется в chat-формате в training/lora_dataset.jsonl.
    final_answer = corrected_answer if (not good and corrected_answer) else pair["vita_answer"]
    try:
        _append_lora_row(pair["user_prompt"], final_answer)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# QuizScreen
# ---------------------------------------------------------------------------

class QuizScreen(BaseScreen):
    """Экран викторины оценки ответов Виты."""

    TITLE = "[ ОЦЕНКА ОТВЕТОВ ВИТЫ ]"

    def __init__(self, parent: tk.Widget, controller, state: Dict[str, Any] | None = None):
        super().__init__(parent, controller)
        self.controller = controller
        self._state = state if state is not None else getattr(controller, '_quiz_state', {})
        controller._quiz_state = self._state
        self._pairs: List[Dict[str, Any]] = []
        self._index: int = 0
        self._good_count: int = 0
        self._bad_count: int = 0
        self._correction_mode: bool = False
        self._reviewed_keys: set[str] = set()
        self._init_keyboard_nav()
        self._build_ui()
        self._load_pairs()
    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.configure(bg=BG)

        # ── Заголовок ──────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=10, pady=(8, 0))

        self._back_btn = tk.Button(
            hdr, text="← Назад", font=FONT_SM, bg=BG, fg=AMBER,
            activebackground=DIM, activeforeground=AMBER,
            relief="flat", cursor="hand2",
            command=self._go_back,
        )
        self._back_btn.pack(side="left")

        tk.Label(hdr, text=self.TITLE, font=FONT_LG, bg=BG, fg=FG).pack(side="left", padx=16)

        self._reset_btn = tk.Button(
            hdr, text="Сбросить прогресс", font=FONT_SM, bg=BG, fg=RED_ERR,
            activebackground=DIM, activeforeground=RED_ERR,
            relief="flat", cursor="hand2",
            command=self._reset_progress,
        )
        self._reset_btn.pack(side="left", padx=8)

        self._counter_var = tk.StringVar(value="0 / 0")
        tk.Label(hdr, textvariable=self._counter_var, font=FONT_SM, bg=BG, fg=AMBER).pack(side="right")

        tk.Frame(self, bg=DIM, height=1).pack(fill="x", padx=10, pady=6)

        # ── Поиск/автопополнение: быстрый переход к нужной паре ────────
        search_frame = tk.Frame(self, bg=BG)
        search_frame.pack(fill="x", padx=12, pady=(0, 2))
        tk.Label(search_frame, text="Поиск пары:", font=FONT_SM, bg=BG, fg=DIM).pack(side="left")
        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._update_search_suggestions())
        self._search_entry = tk.Entry(
            search_frame, textvariable=self._search_var, font=FONT_SM,
            bg="#0d0d0d", fg=AMBER, insertbackground=AMBER, relief="flat", width=44,
        )
        self._search_entry.pack(side="left", padx=6)
        self._search_hint = tk.Label(search_frame, text="", font=FONT_SM, bg=BG, fg=DIM)
        self._search_hint.pack(side="left", padx=6)
        self._suggest_list = tk.Listbox(
            self, font=FONT_SM, bg="#0d0d0d", fg="#aaffaa", relief="flat",
            height=4, highlightthickness=0,
        )
        self._suggest_list.bind("<<ListboxSelect>>", self._jump_to_suggestion)

        # ── Блок вопроса ───────────────────────────────────────────────
        q_frame = tk.LabelFrame(self, text=" Вопрос пользователя ", font=FONT_SM,
                                bg=BG, fg=AMBER, bd=1, relief="solid")
        q_frame.pack(fill="x", padx=12, pady=(4, 2))

        self._question_text = tk.Text(
            q_frame, font=FONT, bg="#0d0d0d", fg="#aaffaa",
            relief="flat", wrap="word", height=4,
            state="disabled", cursor="arrow",
        )
        self._question_text.pack(fill="x", padx=6, pady=6)

        # ── Блок ответа Виты ───────────────────────────────────────────
        a_frame = tk.LabelFrame(self, text=" Ответ Виты ", font=FONT_SM,
                                bg=BG, fg=FG, bd=1, relief="solid")
        a_frame.pack(fill="x", padx=12, pady=(2, 4))

        self._answer_text = tk.Text(
            a_frame, font=FONT, bg="#0d0d0d", fg=FG,
            relief="flat", wrap="word", height=6,
            state="disabled", cursor="arrow",
        )
        self._answer_text.pack(fill="x", padx=6, pady=6)

        # ── Кнопки оценки ─────────────────────────────────────────────
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=6)

        self._good_btn = tk.Button(
            btn_frame, text="✓  Хорошо", font=FONT_LG,
            bg="#003300", fg="#00ff00",
            activebackground="#005500", activeforeground="#00ff00",
            relief="flat", width=14, cursor="hand2",
            command=self._rate_good,
        )
        self._good_btn.pack(side="left", padx=12)

        self._bad_btn = tk.Button(
            btn_frame, text="✗  Плохо", font=FONT_LG,
            bg="#330000", fg=RED_ERR,
            activebackground="#550000", activeforeground=RED_ERR,
            relief="flat", width=14, cursor="hand2",
            command=self._rate_bad,
        )
        self._bad_btn.pack(side="left", padx=12)

        # ── Блок исправления (скрыт по умолчанию) ─────────────────────
        self._correction_frame = tk.LabelFrame(
            self, text=" Введи правильный ответ ", font=FONT_SM,
            bg=BG, fg=RED_ERR, bd=1, relief="solid",
        )

        self._correction_entry = tk.Text(
            self._correction_frame, font=FONT, bg="#1a0000", fg="#ffaaaa",
            relief="flat", wrap="word", height=5,
        )
        self._correction_entry.pack(fill="x", padx=6, pady=4)

        save_row = tk.Frame(self._correction_frame, bg=BG)
        save_row.pack(fill="x", padx=6, pady=(0, 6))

        self._save_correction_btn = tk.Button(
            save_row, text="Сохранить исправление", font=FONT,
            bg="#330000", fg=RED_ERR,
            activebackground="#550000", activeforeground=RED_ERR,
            relief="flat", cursor="hand2",
            command=self._save_correction,
        )
        self._save_correction_btn.pack(side="left")

        self._cancel_correction_btn = tk.Button(
            save_row, text="Отмена", font=FONT_SM,
            bg=BG, fg=AMBER,
            activebackground=DIM, activeforeground=AMBER,
            relief="flat", cursor="hand2",
            command=self._cancel_correction,
        )
        self._cancel_correction_btn.pack(side="left", padx=10)

        # ── Статистика ─────────────────────────────────────────────────
        stat_frame = tk.Frame(self, bg=BG)
        stat_frame.pack(side="bottom", pady=8)

        self._stat_var = tk.StringVar(value="")
        tk.Label(stat_frame, textvariable=self._stat_var, font=FONT_SM, bg=BG, fg=AMBER).pack()

        self._register_focusable(self._back_btn, self._go_back)
        self._register_focusable(self._reset_btn, self._reset_progress)
        self._register_focusable(self._good_btn, self._rate_good)
        self._register_focusable(self._bad_btn, self._rate_bad)
        self._register_focusable(self._save_correction_btn, self._save_correction)
        self._register_focusable(self._cancel_correction_btn, self._cancel_correction)
        self._focus_widget(1)

    # ------------------------------------------------------------------
    # Search / автопополнение
    # ------------------------------------------------------------------

    def _update_search_suggestions(self) -> None:
        query = self._search_var.get().strip().lower()
        if not query:
            self._suggest_list.pack_forget()
            self._search_hint.config(text="")
            return
        matches = [
            (i, p) for i, p in enumerate(self._pairs)
            if query in str(p.get("user_prompt", "")).lower() or query in str(p.get("vita_answer", "")).lower()
        ]
        self._suggest_list.delete(0, tk.END)
        for i, p in matches[:8]:
            preview = " ".join(str(p.get("user_prompt", "")).split())[:70]
            self._suggest_list.insert(tk.END, f"{i + 1:>4}. {preview}")
            self._suggest_list.indexes = getattr(self._suggest_list, "indexes", [])
        self._suggest_list.indexes = [i for i, _ in matches[:8]]
        if matches:
            self._suggest_list.pack(fill="x", padx=12, pady=(0, 2))
            self._search_hint.config(text=f"найдено {len(matches)} — кликни для перехода")
        else:
            self._suggest_list.pack_forget()
            self._search_hint.config(text="не найдено")

    def _jump_to_suggestion(self, _event=None) -> None:
        selection = self._suggest_list.curselection()
        if not selection or not self._suggest_list.indexes:
            return
        idx = self._suggest_list.indexes[selection[0]]
        if 0 <= idx < len(self._pairs):
            self._index = idx
            self._show_pair(self._index)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_pairs(self) -> None:
        reviewed_keys, inferred_good, inferred_bad = _load_reviewed_from_dataset()
        progress = _load_progress_file()
        self._reviewed_keys = set(reviewed_keys) | set(progress.get("reviewed_keys", []))

        dataset_pairs = _load_dataset_pairs()
        dialog_pairs  = _load_dialog_pairs()

        # Диалоговые пары идут первыми (свежие), потом датасет.
        # Список всегда пересобирается: сохранённые pairs не блокируют новые memory.json.
        combined = dialog_pairs + dataset_pairs

        seen: set = set()
        unique: List[Dict[str, Any]] = []
        for p in combined:
            p.setdefault("pair_id", _pair_id(p))
            key = _pair_key(p)
            if key in seen or key in self._reviewed_keys or p["pair_id"] in self._reviewed_keys:
                continue
            seen.add(key)
            unique.append(p)

        self._pairs = unique
        # FIX (запрос «где я остановился — сохраняется»): восстанавливаем позицию
        # из прогресса, а не всегда начинаем с первой пары.
        saved_index = int(progress.get("index", 0) or 0)
        self._index = min(max(saved_index, 0), len(unique)) if unique else 0
        self._good_count = int(progress.get("good_count", inferred_good))
        self._bad_count = int(progress.get("bad_count", inferred_bad))

        # Синхронизация LoRA-файла со всеми оценками из dataset.jsonl (best-effort).
        try:
            rebuild_lora_dataset()
        except Exception:
            pass
        self._save_state()
        if self._pairs:
            if self._index >= len(self._pairs):
                self._show_done()
            else:
                self._show_pair(self._index)
        else:
            self._show_empty()

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _show_pair(self, idx: int) -> None:
        self._correction_mode = False
        self._correction_frame.pack_forget()
        self._good_btn.config(state="normal")
        self._bad_btn.config(state="normal")

        pair = self._pairs[idx]
        total = len(self._pairs)
        self._counter_var.set(f"{idx + 1} / {total}")
        self._stat_var.set(f"✓ {self._good_count}   ✗ {self._bad_count}")

        self._set_text(self._question_text, pair["user_prompt"])
        self._set_text(self._answer_text,   pair["vita_answer"])

    def _show_empty(self) -> None:
        self._counter_var.set("0 / 0")
        self._set_text(self._question_text, "(нет данных для оценки)")
        self._set_text(self._answer_text,   "Запусти Виту и поговори с ней — пары появятся здесь.")
        self._good_btn.config(state="disabled")
        self._bad_btn.config(state="disabled")

    def _show_done(self) -> None:
        self._counter_var.set("Готово!")
        self._set_text(self._question_text, "Все пары оценены.")
        self._set_text(
            self._answer_text,
            f"Результаты сохранены в training/dataset.jsonl\n\n"
            f"✓ Хороших ответов: {self._good_count}\n"
            f"✗ Плохих ответов:  {self._bad_count}",
        )
        self._good_btn.config(state="disabled")
        self._bad_btn.config(state="disabled")
        self._correction_frame.pack_forget()

    @staticmethod
    def _set_text(widget: tk.Text, value: str) -> None:
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.config(state="disabled")

    # ------------------------------------------------------------------
    # Rating actions
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        pair_keys = [_pair_key(p) for p in self._pairs]
        pair_ids = [p.get("pair_id") or _pair_id(p) for p in self._pairs]
        self._state.update({
            "pairs": self._pairs,
            "index": self._index,
            "good_count": self._good_count,
            "bad_count": self._bad_count,
            "reviewed_keys": list(self._reviewed_keys),
            "memory": _memory_signature(),
            "dataset": _dataset_signature(),
        })
        self.controller._quiz_state = self._state
        _save_progress_file({
            "version": 2,
            "index": self._index,
            "good_count": self._good_count,
            "bad_count": self._bad_count,
            "reviewed_keys": list(self._reviewed_keys),
            "reviewed_count": len(self._reviewed_keys),
            "pair_keys": pair_keys,
            "pair_ids": pair_ids,
            "memory": _memory_signature(),
            "dataset": _dataset_signature(),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    def _rate_good(self) -> None:
        if not self._pairs or self._index >= len(self._pairs):
            return
        pair = self._pairs[self._index]
        self._reviewed_keys.add(_pair_key(pair))
        self._reviewed_keys.add(pair.get("pair_id") or _pair_id(pair))
        _save_rating(pair, good=True)
        self._good_count += 1
        self._save_state()
        self._next_pair()

    def _rate_bad(self) -> None:
        if not self._pairs or self._index >= len(self._pairs):
            return
        # Показываем поле для исправления
        self._correction_mode = True
        self._correction_entry.delete("1.0", "end")
        self._correction_frame.pack(fill="x", padx=12, pady=(2, 4))
        self._correction_entry.focus_set()
        self._good_btn.config(state="disabled")
        self._bad_btn.config(state="disabled")

    def _save_correction(self) -> None:
        corrected = self._correction_entry.get("1.0", "end").strip()
        if not corrected:
            messagebox.showwarning("Пусто", "Введи правильный ответ перед сохранением.", parent=self)
            return
        pair = self._pairs[self._index]
        self._reviewed_keys.add(_pair_key(pair))
        self._reviewed_keys.add(pair.get("pair_id") or _pair_id(pair))
        _save_rating(pair, good=False, corrected_answer=corrected)
        self._bad_count += 1
        self._save_state()
        self._correction_mode = False
        self._correction_frame.pack_forget()
        self._next_pair()

    def _cancel_correction(self) -> None:
        self._correction_mode = False
        self._correction_frame.pack_forget()
        self._good_btn.config(state="normal")
        self._bad_btn.config(state="normal")

    def _next_pair(self) -> None:
        self._index += 1
        self._save_state()
        if self._index >= len(self._pairs):
            self._show_done()
        else:
            self._show_pair(self._index)


    def _reset_progress(self) -> None:
        if not messagebox.askyesno("Сбросить прогресс", "Начать викторину с первой пары? Оценки в dataset.jsonl не удаляются.", parent=self):
            return
        try:
            if PROGRESS_PATH.exists():
                PROGRESS_PATH.unlink()
        except OSError:
            pass
        self._state.clear()
        self.controller._quiz_state = self._state
        self._index = 0
        self._good_count = 0
        self._bad_count = 0
        self._reviewed_keys = set()
        self._save_state()
        if self._pairs:
            self._show_pair(0)
        else:
            self._show_empty()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_back_key(self, event=None):
        if self._correction_mode:
            self._cancel_correction()
            return "break"
        return super()._go_back_key(event)

    def _go_back(self) -> None:
        self._save_state()
        if hasattr(self.controller, "show_main_menu"):
            self.controller.show_main_menu()
        elif hasattr(self.controller, "show_frame"):
            self.controller.show_frame("main")




