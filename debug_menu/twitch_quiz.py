from __future__ import annotations

import hashlib
import json
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any, Dict, Iterable, List, Set

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .config import AMBER, BG, DIM, FG, FONT, FONT_LG, FONT_SM, RED_ERR, PROJECT_ROOT
from .ui import BaseScreen

MEMORY_PATH = PROJECT_ROOT / "memory.json"
DATASET_PATH = PROJECT_ROOT / "training" / "dataset.jsonl"
TWITCH_INTERACTIONS_PATH = PROJECT_ROOT / "training" / "twitch_interactions.jsonl"
PROGRESS_PATH = PROJECT_ROOT / "cache" / "debug_twitch_quiz_progress.json"
DATASET_SOURCE = "twitch_quiz_rating"

TWITCH_MARKERS = ("twitch", "твич")
USER_KEYS = ("twitch_user", "username", "user", "author")
MESSAGE_KEYS = ("twitch_message", "message", "text", "user_prompt")
ANSWER_KEYS = ("vita_answer", "response", "answer", "bot_response")
META_KEYS = ("source", "origin", "channel", "type")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains_twitch(value: Any) -> bool:
    return any(marker in _text(value).lower() for marker in TWITCH_MARKERS)


def _walk(obj: Any) -> Iterable[Any]:
    yield obj
    if isinstance(obj, dict):
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _walk_lists(obj: Any) -> Iterable[List[Any]]:
    if isinstance(obj, list):
        yield obj
        for item in obj:
            yield from _walk_lists(item)
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _walk_lists(value)


def _first(entry: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = entry.get(key)
        if value is not None and _text(value):
            return _text(value)
    return ""


def _is_twitch_dict(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    for key in META_KEYS:
        if _contains_twitch(entry.get(key)):
            return True
    if _first(entry, ("twitch_user", "twitch_message")):
        return True
    return False


def _role(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    return _text(entry.get("role") or entry.get("speaker") or entry.get("author_type")).lower()


def _entry_text(entry: Any) -> str:
    if isinstance(entry, dict):
        return _first(entry, ("text", "message", "content", "user_prompt", "response", "answer", "bot_response"))
    return _text(entry)


def _pair_id(pair: Dict[str, Any]) -> str:
    raw = "\n".join([
        _text(pair.get("twitch_user")),
        _text(pair.get("twitch_message") or pair.get("user_prompt")),
        _text(pair.get("vita_answer")),
    ])
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _make_pair(user: str, message: str, answer: str, original: Any = None) -> Dict[str, Any] | None:
    message = _text(message)
    answer = _text(answer)
    if not message or not answer or message == answer:
        return None
    pair = {
        "twitch_user": _text(user) or "не указан",
        "twitch_message": message,
        "user_prompt": message,
        "context": f"Twitch user {_text(user)}: {message}" if _text(user) else f"Twitch: {message}",
        "vita_answer": answer,
        "original": original,
    }
    pair["pair_id"] = _pair_id(pair)
    return pair


def load_twitch_pairs() -> List[Dict[str, Any]]:
    memory = _read_json(MEMORY_PATH, {})
    pairs: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def add(pair: Dict[str, Any] | None) -> None:
        if not pair:
            return
        pid = pair["pair_id"]
        if pid not in seen:
            seen.add(pid)
            pairs.append(pair)

    for node in _walk(memory):
        if not _is_twitch_dict(node):
            continue
        user = _first(node, USER_KEYS)
        message = _first(node, MESSAGE_KEYS)
        answer = _first(node, ANSWER_KEYS)
        add(_make_pair(user, message, answer, node))

    if TWITCH_INTERACTIONS_PATH.exists():
        for line in TWITCH_INTERACTIONS_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                add(_make_pair(_first(row, USER_KEYS), _first(row, MESSAGE_KEYS), _first(row, ANSWER_KEYS), row))

    for seq in _walk_lists(memory):
        for idx, entry in enumerate(seq):
            if not _is_twitch_dict(entry):
                continue
            user = _first(entry, USER_KEYS) if isinstance(entry, dict) else ""
            message = _first(entry, MESSAGE_KEYS) if isinstance(entry, dict) else _entry_text(entry)
            if not message:
                continue
            for nxt in seq[idx + 1: idx + 5]:
                role = _role(nxt)
                answer = _entry_text(nxt)
                if answer and (role in ("vita", "вита", "assistant", "bot") or not _is_twitch_dict(nxt)):
                    add(_make_pair(user, message, answer, {"twitch": entry, "vita": nxt}))
                    break
    return pairs


def load_reviewed_ids() -> Set[str]:
    progress = _read_json(PROGRESS_PATH, {})
    if isinstance(progress, dict) and progress.get("reviewed_ids"):
        return {str(x) for x in progress.get("reviewed_ids", [])}
    reviewed: Set[str] = set()
    if DATASET_PATH.exists():
        for line in DATASET_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("source") != DATASET_SOURCE:
                continue
            pid = row.get("pair_id") or _pair_id(row)
            if pid:
                reviewed.add(str(pid))
    return reviewed


def save_progress(reviewed_ids: Set[str]) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "reviewed_ids": sorted(reviewed_ids),
    }
    PROGRESS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class TwitchQuizScreen(BaseScreen):
    TITLE = "ОЦЕНКА TWITCH-ОТВЕТОВ"

    def __init__(self, master: tk.Widget, app: Any):
        super().__init__(master, app)
        self._init_keyboard_nav()
        self.pairs = load_twitch_pairs()
        self.reviewed_ids = load_reviewed_ids()
        self.current: Dict[str, Any] | None = None
        self._build()
        self._show_next()

    def _build(self) -> None:
        self.configure(bg=BG)
        self._make_header(self.TITLE)
        self.progress_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.message_var = tk.StringVar()
        self.answer_var = tk.StringVar()
        self.status_var = tk.StringVar()

        tk.Label(self, textvariable=self.progress_var, bg=BG, fg=AMBER, font=FONT_SM).pack(anchor="w")
        tk.Label(self, text="Twitch-пользователь:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w", pady=(8, 0))
        tk.Label(self, textvariable=self.user_var, bg=BG, fg=AMBER, font=FONT, wraplength=620, justify="left").pack(anchor="w")
        tk.Label(self, text="Сообщение Twitch:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w", pady=(8, 0))
        tk.Label(self, textvariable=self.message_var, bg=DIM, fg=FG, font=FONT, wraplength=620, justify="left", padx=8, pady=6).pack(fill="x")
        tk.Label(self, text="Ответ Виты:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w", pady=(8, 0))
        tk.Label(self, textvariable=self.answer_var, bg=DIM, fg=FG, font=FONT, wraplength=620, justify="left", padx=8, pady=6).pack(fill="x")

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(pady=(10, 4))
        self.good_btn = tk.Button(buttons, text="[ ✓ Хорошо ]", bg=BG, fg=AMBER, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._mark_good)
        self.bad_btn = tk.Button(buttons, text="[ ✗ Плохо ]", bg=BG, fg=RED_ERR, font=FONT, activebackground=DIM, activeforeground=RED_ERR, relief="flat", cursor="hand2", command=self._show_bad_field)
        self.reset_btn = tk.Button(buttons, text="[ Сбросить прогресс ]", bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._reset_progress)
        self.good_btn.grid(row=0, column=0, padx=5)
        self.bad_btn.grid(row=0, column=1, padx=5)
        self.reset_btn.grid(row=0, column=2, padx=5)

        self.bad_frame = tk.Frame(self, bg=BG)
        tk.Label(self.bad_frame, text="Правильный ответ:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w")
        self.correct_text = tk.Text(self.bad_frame, height=4, width=70, bg=DIM, fg=AMBER, insertbackground=AMBER, font=FONT_SM, wrap="word")
        self.correct_text.pack(fill="x")
        self.save_bad_btn = tk.Button(self.bad_frame, text="[ Сохранить исправление ]", bg=BG, fg=AMBER, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._mark_bad)
        self.save_bad_btn.pack(pady=(4, 0))

        tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG, font=FONT_SM, wraplength=620, justify="left").pack(pady=(6, 0), anchor="w")
        self._make_back_button()

        for widget, action in ((self.good_btn, self._mark_good), (self.bad_btn, self._show_bad_field), (self.reset_btn, self._reset_progress), (self.save_bad_btn, self._mark_bad)):
            self._register_focusable(widget, action)
        self._focus_widget(0)

    def _remaining(self) -> List[Dict[str, Any]]:
        return [pair for pair in self.pairs if pair.get("pair_id") not in self.reviewed_ids]

    def _show_next(self) -> None:
        remaining = self._remaining()
        total = len(self.pairs)
        self.progress_var.set(f"Проверено: {total - len(remaining)}/{total} • прогресс: {PROGRESS_PATH}")
        if self.bad_frame.winfo_ismapped():
            self.bad_frame.pack_forget()
        self.correct_text.delete("1.0", tk.END)
        if not remaining:
            self.current = None
            self.user_var.set("—")
            self.message_var.set("Нет непроверенных Twitch-пар.")
            self.answer_var.set("Если список пуст, в memory.json не найдены записи с Twitch-метаданными.")
            self.status_var.set("Готово. Сброс прогресса не удаляет записи из training/dataset.jsonl.")
            return
        self.current = remaining[0]
        self.user_var.set(self.current.get("twitch_user") or "не указан")
        self.message_var.set(self.current.get("twitch_message") or "")
        self.answer_var.set(self.current.get("vita_answer") or "")
        self.status_var.set("Оцените ответ: хорошо или плохо.")

    def _append_rating(self, rating: str, corrected: str = "") -> None:
        if not self.current:
            return
        DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
        row: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": DATASET_SOURCE,
            "pair_id": self.current["pair_id"],
            "twitch_user": self.current.get("twitch_user", ""),
            "twitch_message": self.current.get("twitch_message", ""),
            "user_prompt": self.current.get("user_prompt", ""),
            "context": self.current.get("context", ""),
            "vita_answer": self.current.get("vita_answer", ""),
            "rating": rating,
        }
        if rating == "bad":
            row["bad_previous_vita_answer"] = self.current.get("vita_answer", "")
            row["corrected_answer"] = corrected
        else:
            row["corrected_answer"] = self.current.get("vita_answer", "")
        with DATASET_PATH.open("a", encoding="utf-8") as file:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.reviewed_ids.add(self.current["pair_id"])
        save_progress(self.reviewed_ids)
        self._show_next()

    def _mark_good(self) -> None:
        self._append_rating("good")

    def _show_bad_field(self) -> None:
        if not self.current:
            return
        if not self.bad_frame.winfo_ismapped():
            self.bad_frame.pack(fill="x", pady=(6, 0), before=list(self.children.values())[-1])
        self.correct_text.focus_set()
        self.status_var.set("Введите исправленный ответ и нажмите [ Сохранить исправление ].")

    def _mark_bad(self) -> None:
        corrected = self.correct_text.get("1.0", tk.END).strip()
        if not corrected:
            messagebox.showwarning("Нужно исправление", "Для плохой оценки введите правильный ответ.")
            return
        self._append_rating("bad", corrected)

    def _reset_progress(self) -> None:
        if not messagebox.askyesno("Сброс прогресса", "Сбросить только прогресс Twitch-викторины? Записи dataset.jsonl останутся."):
            return
        self.reviewed_ids = set()
        save_progress(self.reviewed_ids)
        self._show_next()


def show_twitch_quiz(master: tk.Widget, app: Any) -> TwitchQuizScreen:
    return TwitchQuizScreen(master, app)
