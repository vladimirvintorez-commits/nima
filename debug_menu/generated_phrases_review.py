from __future__ import annotations

import hashlib
import json
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any, Dict, List, Set, Tuple

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .config import AMBER, BG, DIM, FG, FONT, FONT_SM, RED_ERR, PROJECT_ROOT
from .ui import BaseScreen

GENERATED_PATH = PROJECT_ROOT / "cache" / "generated_phrases.json"
PREPARED_PATH = PROJECT_ROOT / "data" / "prepared_phrases.json"
REVIEW_PATH = PROJECT_ROOT / "cache" / "generated_phrases_review.json"
APPROVED_FALLBACK_PATH = PROJECT_ROOT / "data" / "approved_generated_phrases.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _phrase_id(category: str, phrase: str) -> str:
    raw = json.dumps([category, phrase], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_review() -> Dict[str, Any]:
    data = _read_json(REVIEW_PATH, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("reviewed_ids", [])
    data.setdefault("items", {})
    return data


def _save_review(data: Dict[str, Any]) -> None:
    data["updated_at"] = _now()
    _write_json(REVIEW_PATH, data)


def _category_target(category: str, prepared: Dict[str, Any]) -> Tuple[List[str] | None, str]:
    quick = prepared.get("quick_phrases")
    if isinstance(quick, dict) and isinstance(quick.get(category), list):
        return ["quick_phrases", category], f"quick_phrases.{category}"

    if category == "animation_spin_around":
        return ["quick_phrases", "animation_spin_around"], "quick_phrases.animation_spin_around"
    if category == "outfit_spin":
        return ["outfit", "spin"], "outfit.spin"
    if category.startswith("outfit_change:"):
        name = category.split(":", 1)[1].strip()
        if name:
            return ["outfit", "change", name], f"outfit.change[{name}]"
    if category.startswith("outfit_"):
        sub = category.split("_", 1)[1]
        if isinstance(prepared.get("outfit"), dict) and isinstance(prepared["outfit"].get(sub), list):
            return ["outfit", sub], f"outfit.{sub}"
    return None, "fallback"


def _get_list(root: Dict[str, Any], path: List[str]) -> List[str] | None:
    node: Any = root
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.setdefault(key, {})
    if isinstance(node, list):
        return node
    return None


def _append_to_prepared(category: str, phrase: str) -> str:
    prepared = _read_json(PREPARED_PATH, {})
    if not isinstance(prepared, dict):
        prepared = {}
    path, label = _category_target(category, prepared)
    if not path:
        return _append_to_fallback(category, phrase, "unknown_category")
    target = _get_list(prepared, path)
    if target is None:
        return _append_to_fallback(category, phrase, "target_not_list")
    if phrase not in target:
        target.append(phrase)
        _write_json(PREPARED_PATH, prepared)
    return str(PREPARED_PATH.relative_to(PROJECT_ROOT)) + " -> " + label


def _append_to_fallback(category: str, phrase: str, reason: str) -> str:
    data = _read_json(APPROVED_FALLBACK_PATH, {})
    if not isinstance(data, dict):
        data = {}
    bucket = data.setdefault(category, [])
    if isinstance(bucket, list) and phrase not in bucket:
        bucket.append(phrase)
    meta = data.setdefault("_meta", {})
    if isinstance(meta, dict):
        meta[category] = {"reason": reason, "updated_at": _now()}
    _write_json(APPROVED_FALLBACK_PATH, data)
    return str(APPROVED_FALLBACK_PATH.relative_to(PROJECT_ROOT)) + f" ({reason})"


class GeneratedPhrasesReviewScreen(BaseScreen):
    TITLE = "ОЦЕНКА СГЕНЕРИРОВАННЫХ РЕПЛИК"

    def __init__(self, master: tk.Widget, app: Any):
        super().__init__(master, app)
        self._init_keyboard_nav()
        self.generated: Dict[str, List[str]] = {}
        self.review = _load_review()
        self.categories: List[str] = []
        self.current_category = ""
        self.current_phrase = ""
        self.current_id = ""
        self.current_queue_item: Dict[str, str] | None = None
        self._build()
        self._reload()

    def _build(self) -> None:
        self.configure(bg=BG)
        self._make_header(self.TITLE)
        self.progress_var = tk.StringVar()
        self.category_var = tk.StringVar()
        self.status_var = tk.StringVar()

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x")
        tk.Label(top, text="Единая очередь новых фраз", bg=BG, fg=FG, font=FONT_SM).pack(side="left")
        tk.Label(top, textvariable=self.progress_var, bg=BG, fg=AMBER, font=FONT_SM).pack(side="right")

        tk.Label(self, text="Сгенерированная фраза:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w", pady=(8, 0))
        self.phrase_text = tk.Text(self, height=7, width=78, bg=DIM, fg=AMBER, insertbackground=AMBER, font=FONT, wrap="word")
        self.phrase_text.pack(fill="x")

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(pady=(10, 4))
        self.approve_btn = tk.Button(buttons, text="[ ✓ Подтвердить ]", bg=BG, fg=AMBER, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._approve)
        self.reject_btn = tk.Button(buttons, text="[ ✗ Отклонить ]", bg=BG, fg=RED_ERR, font=FONT, activebackground=DIM, activeforeground=RED_ERR, relief="flat", cursor="hand2", command=self._reject)
        self.edit_btn = tk.Button(buttons, text="[ ✎ Сохранить правку ]", bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._approve_edit)
        self.skip_btn = tk.Button(buttons, text="[ Пропустить ]", bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._skip)
        self.listen_btn = tk.Button(buttons, text="[ 🔊 Прослушать ]", bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._speak_current)
        self.regen_btn = tk.Button(buttons, text="[ ↻ Перегенерировать ]", bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._regenerate_current)
        self.approve_btn.grid(row=0, column=0, padx=5)
        self.reject_btn.grid(row=0, column=1, padx=5)
        self.edit_btn.grid(row=0, column=2, padx=5)
        self.skip_btn.grid(row=0, column=3, padx=5)
        self.listen_btn.grid(row=0, column=4, padx=5)
        self.regen_btn.grid(row=0, column=5, padx=5)

        tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG, font=FONT_SM, wraplength=680, justify="left").pack(anchor="w", pady=(6, 0))
        self._make_back_button()
        for widget, action in ((self.approve_btn, self._approve), (self.reject_btn, self._reject), (self.edit_btn, self._approve_edit), (self.skip_btn, self._skip)):
            self._register_focusable(widget, action)
        self._focus_widget(0)

    def _reload(self) -> None:
        raw = _read_json(GENERATED_PATH, {})
        self.generated = {str(k): [str(x) for x in v if str(x).strip()] for k, v in raw.items() if isinstance(v, list)} if isinstance(raw, dict) else {}
        self.categories = sorted(self.generated)
        self._sync_review_queue()
        self._show_next(restore=True)

    def _reviewed_ids(self) -> Set[str]:
        return {str(x) for x in self.review.get("reviewed_ids", [])}

    def _queue(self) -> List[Dict[str, str]]:
        queue = self.review.setdefault("queue", [])
        if not isinstance(queue, list):
            queue = []
            self.review["queue"] = queue
        return queue

    def _live_ids(self) -> Set[str]:
        return {_phrase_id(category, phrase) for category, phrases in self.generated.items() for phrase in phrases}

    def _sync_review_queue(self) -> None:
        reviewed = self._reviewed_ids()
        existing = {str(item.get("id")) for item in self._queue() if isinstance(item, dict)}
        changed = False
        for category in self.categories:
            for phrase in self.generated.get(category, []):
                pid = _phrase_id(category, phrase)
                if pid in reviewed or pid in existing:
                    continue
                self._queue().append({"id": pid, "category": category, "phrase": phrase, "added_at": _now()})
                existing.add(pid)
                changed = True
        if "current_category" in self.review:
            self.review.pop("current_category", None)
            changed = True
        if changed or "current_index" not in self.review:
            self.review.setdefault("current_index", 0)
            _save_review(self.review)

    def _remaining(self) -> List[Dict[str, str]]:
        reviewed = self._reviewed_ids()
        live = self._live_ids()
        return [item for item in self._queue() if isinstance(item, dict) and item.get("id") in live and item.get("id") not in reviewed]

    def _remove_from_generated(self, category: str, phrase: str) -> None:
        raw = _read_json(GENERATED_PATH, {})
        if not isinstance(raw, dict):
            return
        values = raw.get(category)
        if not isinstance(values, list):
            return
        removed = False
        kept = []
        for value in values:
            if not removed and str(value) == phrase:
                removed = True
                continue
            kept.append(value)
        if removed:
            raw[category] = kept
            _write_json(GENERATED_PATH, raw)
            self.generated[category] = [str(x) for x in kept if str(x).strip()]

    def _show_next(self, restore: bool = False) -> None:
        remaining = self._remaining()
        total = len(self._live_ids())
        self.progress_var.set(f"Очередь: {len(remaining)}/{total} • журнал: {REVIEW_PATH.relative_to(PROJECT_ROOT)}")
        self.phrase_text.delete("1.0", tk.END)
        if not remaining:
            self.current_category = self.current_phrase = self.current_id = ""
            self.current_queue_item = None
            self.phrase_text.insert("1.0", "Нет новых непроверенных фраз в cache/generated_phrases.json.")
            self.status_var.set("Новые фразы будут добавлены в конец единой очереди без сброса прогресса.")
            self.review["current_index"] = 0
            self.review.pop("current_category", None)
            _save_review(self.review)
            return
        index = int(self.review.get("current_index", 0) or 0)
        index = max(0, min(index, len(remaining) - 1))
        item = remaining[index]
        category = str(item.get("category", ""))
        self.current_queue_item = item
        self.current_category = category
        self.current_phrase = str(item.get("phrase", ""))
        self.current_id = str(item.get("id", _phrase_id(category, self.current_phrase)))
        self.phrase_text.insert("1.0", self.current_phrase)
        self.review["current_index"] = index
        self.review.pop("current_category", None)
        _save_review(self.review)
        path, label = _category_target(category, _read_json(PREPARED_PATH, {}))
        self.status_var.set(f"Категория: {category}. Будет сохранена в {label if path else APPROVED_FALLBACK_PATH.relative_to(PROJECT_ROOT)}. Можно отредактировать текст прямо в поле.")

    def _mark_reviewed(self, status: str, saved_to: str = "", edited_text: str = "") -> None:
        if not self.current_id:
            return
        reviewed = self._reviewed_ids()
        reviewed.add(self.current_id)
        self.review["reviewed_ids"] = sorted(reviewed)
        self.review.setdefault("items", {})[self.current_id] = {
            "category": self.current_category,
            "original": self.current_phrase,
            "text": edited_text or self.current_phrase,
            "status": status,
            "saved_to": saved_to,
            "reviewed_at": _now(),
        }
        if status in {"approved", "edited", "rejected"}:
            self._remove_from_generated(self.current_category, self.current_phrase)
        self.review.pop("current_category", None)
        self.review["current_index"] = int(self.review.get("current_index", 0) or 0)
        _save_review(self.review)
        self._reload()

    def _approve(self) -> None:
        if not self.current_id:
            return
        text = self.phrase_text.get("1.0", tk.END).strip() or self.current_phrase
        saved_to = _append_to_prepared(self.current_category, text)
        self._mark_reviewed("approved", saved_to, text)

    def _approve_edit(self) -> None:
        if not self.current_id:
            return
        text = self.phrase_text.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Пустая фраза", "Введите текст фразы или отклоните её.")
            return
        saved_to = _append_to_prepared(self.current_category, text)
        self._mark_reviewed("edited", saved_to, text)

    def _reject(self) -> None:
        if not self.current_id:
            return
        self._mark_reviewed("rejected")

    def _skip(self) -> None:
        if not self.current_id:
            return
        remaining = self._remaining()
        if remaining:
            self.review["current_index"] = (int(self.review.get("current_index", 0) or 0) + 1) % len(remaining)
            self.review.pop("current_category", None)
            _save_review(self.review)
        self._show_next(restore=True)

    def _speak_current(self) -> None:
        """Озвучивает текст из поля через TTS Нейрони (в фоне, не блокируя GUI)."""
        text = self.phrase_text.get("1.0", tk.END).strip()
        if not text:
            return

        def _worker():
            try:
                from voice.tts_simulator import TTSModule
                TTSModule.speak(text)
            except Exception as exc:
                self.after(0, lambda e=exc: self.status_var.set(f"TTS недоступен: {e}"))

        threading.Thread(target=_worker, daemon=True, name="DebugTTS").start()

    def _regenerate_current(self) -> None:
        """Просит LLM заново сгенерировать фразу этой категории и кладёт в поле."""
        if not self.current_category:
            messagebox.showinfo("Нечего перегенерировать", "Очередь пуста.")
            return
        category = self.current_category
        self.regen_btn.config(state="disabled")
        self.status_var.set("Перегенерация через LLM…")

        def _worker():
            try:
                from ai.ai_module import AIModule
                from ai.phrase_pregenerator import QUICK_PHRASE_CATEGORIES
                purpose = QUICK_PHRASE_CATEGORIES.get(category, QUICK_PHRASE_CATEGORIES["thinking"])
                prompt = (
                    f"Сгенерируй одну короткую русскую фразу Нимфеи для категории: {category}.\n"
                    f"Назначение: {purpose}.\n"
                    "Стиль: Нимфея — рыжая лисодевочка, саркастичная цундере, не помощник.\n"
                    "Только женский род от первого лица. До 12 слов, без markdown, emoji, кавычек.\n"
                    "Верни только фразу."
                )
                raw = str(AIModule().generate_response(prompt) or "").strip()
                phrase = raw.splitlines()[0].strip().strip('"«»') if raw else ""
                def _apply():
                    self.regen_btn.config(state="normal")
                    if phrase:
                        self.phrase_text.delete("1.0", tk.END)
                        self.phrase_text.insert("1.0", phrase)
                        self.status_var.set(f"Перегенерировано (категория {category}). Проверьте и сохраните правку.")
                    else:
                        self.status_var.set("LLM вернула пустой ответ — попробуйте ещё раз.")
                self.after(0, _apply)
            except Exception as exc:
                self.after(0, lambda e=exc: (self.regen_btn.config(state="normal"),
                                             self.status_var.set(f"Перегенерация не удалась: {e}")))

        threading.Thread(target=_worker, daemon=True, name="DebugRegen").start()


def show_generated_phrases_review(master: tk.Widget, app: Any) -> GeneratedPhrasesReviewScreen:
    return GeneratedPhrasesReviewScreen(master, app)
