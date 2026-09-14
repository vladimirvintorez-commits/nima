"""Экран [ СМОК-ТЕСТ ]: фразы из docs/SMOKE_CHECK.md как интерактивный чек-лист.

Задачи чек-листа (v6.27.0): вкладка смок-теста для быстрой проверки механик
голосом + кнопка открытия этого окна из подвкладки БАГИ.
Прогресс отмечается кнопкой состояния (✓ работает / ✗ не работает) и
сохраняется в cache/debug_smoke_progress.json.
Кнопка [ТЕСТ] (v9.0.1) отправляет фразу пункта как ручной ввод пользователя
через cache/manual_commands.json — не надо диктовать её микрофоном.
"""
from __future__ import annotations

import json
import re
import tkinter as tk
from pathlib import Path

from .config import AMBER, BG, DIM, FG, FONT, FONT_SM, PROJECT_ROOT, RED_ERR

SMOKE_PATH = PROJECT_ROOT / "docs" / "SMOKE_CHECK.md"
PROGRESS_PATH = PROJECT_ROOT / "cache" / "debug_smoke_progress.json"

# Состояния отметки пункта: "" (не проверено) -> "ok" (✓) -> "fail" (✗) -> "".
STATUS_SYMBOLS = {"": "·", "ok": "✓", "fail": "✗"}
STATUS_COLORS = {"": DIM, "ok": FG, "fail": RED_ERR}

_ROW_RE = re.compile(r"^\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$")
_IGNORE_RE = re.compile(r"^\s*[|:-]+\s*$")


def load_smoke_items() -> list[dict]:
    """Парсит таблицы SMOKE_CHECK.md в плоский список шагов с секциями."""
    items: list[dict] = []
    section = ""
    if not SMOKE_PATH.exists():
        return items
    for line in SMOKE_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
        header = re.match(r"^#{1,3}\s+(.*)$", line)
        if header:
            section = header.group(1).strip()
            continue
        match = _ROW_RE.match(line.strip())
        if not match or _IGNORE_RE.match(line.strip()):
            continue
        what, phrase, expect = (part.strip() for part in match.groups())
        if what.lower() == "что проверить":
            continue
        items.append({"section": section, "what": what, "phrase": phrase, "expect": expect})
    return items


def _load_progress() -> dict:
    try:
        raw = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # Совместимость со старым форматом чекбоксов (True) -> "ok".
    return {k: ("ok" if v is True else v) for k, v in raw.items()}


def _save_progress(data: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class SmokeTestScreen(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app
        self.items = load_smoke_items()
        self.progress = _load_progress()
        self._vars: list[tuple[tk.BooleanVar, dict]] = []
        self._status_buttons: list[tuple[tk.Button, dict]] = []
        self._build()
        self._make_back_button()

    def _make_back_button(self):
        tk.Button(self, text="[ ← НАЗАД ]", bg=BG, fg=FG, font=FONT, activebackground=DIM,
                  activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self.app.show_main_menu).pack(pady=(8, 0))

    def _build(self):
        tk.Label(self, text="╔══════════════════════════════════════╗", bg=BG, fg=FG, font=FONT).pack()
        tk.Label(self, text="║  СМОК-ТЕСТ МЕХАНИК                   ║", bg=BG, fg=AMBER, font=FONT).pack()
        tk.Label(self, text="╚══════════════════════════════════════╝", bg=BG, fg=FG, font=FONT).pack(pady=(0, 4))
        self.progress_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.progress_var, bg=BG, fg=AMBER, font=FONT_SM).pack()

        outer = tk.Frame(self, bg=BG); outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=BG, highlightthickness=0)
        vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=BG)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True); vsb.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(-1 * (e.delta // 120), "units"))

        last_section = None
        for item in self.items:
            if item["section"] != last_section:
                last_section = item["section"]
                tk.Label(inner, text=f"— {item['section']} —", bg=BG, fg=DIM, font=FONT_SM,
                         anchor="w").pack(fill="x", pady=(6, 1))
            row = tk.Frame(inner, bg=BG); row.pack(fill="x", anchor="w")
            # Кнопка состояния: клик циклит  ·  ->  ✓  ->  ✗  ->  ·
            status_btn = tk.Button(row, width=3, font=FONT_SM, relief="flat", cursor="hand2",
                                   bg=BG)
            var = tk.BooleanVar(value=self._status_of(item) != "")
            status_btn.config(
                text=STATUS_SYMBOLS.get(self._status_of(item), "·"),
                fg=STATUS_COLORS.get(self._status_of(item), DIM),
                command=lambda it=item, sb=status_btn: self._cycle_status(it, sb),
            )
            status_btn.pack(side="left")
            self._status_buttons.append((status_btn, item))
            self._vars.append((var, item))
            tk.Label(row, text=f"{item['what']}:  «{item['phrase']}»", bg=BG, fg=FG, font=FONT_SM,
                     anchor="w").pack(side="left")
            # Кнопка ТЕСТ: отправить фразу как ручной ввод пользователя.
            tk.Button(
                row, text="[ТЕСТ]", font=FONT_SM, relief="flat", cursor="hand2",
                bg=BG, fg=AMBER, activebackground=DIM, activeforeground=AMBER,
                command=lambda it=item: self._send_test_phrase(it),
            ).pack(side="right", padx=(8, 0))
            tk.Label(inner, text=f"   ожидание: {item['expect']}", bg=BG, fg=DIM, font=FONT_SM,
                     anchor="w").pack(fill="x")
        if not self.items:
            tk.Label(inner, text=f"SMOKE_CHECK.md не найден или пуст:\n{SMOKE_PATH}",
                     bg=BG, fg=FG, font=FONT_SM).pack(pady=8)

        btn_row = tk.Frame(self, bg=BG); btn_row.pack(pady=4)
        tk.Button(btn_row, text="[ АВТОТЕСТ ]", bg=BG, fg=AMBER, font=FONT_SM,
                  activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self._run_auto_test).pack(side="left", padx=4)
        tk.Button(btn_row, text="[ СБРОСИТЬ ОТМЕТКИ ]", bg=BG, fg=FG, font=FONT_SM,
                  activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self._reset).pack(side="left", padx=4)
        self._refresh_progress()

    def _run_auto_test(self):
        """АВТОТЕСТ: офлайн-механики проверяются одна за другой (отдельное окно)."""
        from .auto_test import open_auto_test_window
        open_auto_test_window(self)

    @staticmethod
    def _key(item: dict) -> str:
        return f"{item['section']}|{item['what']}"

    def _status_of(self, item: dict) -> str:
        value = self.progress.get(self._key(item), "")
        return value if value in STATUS_SYMBOLS else ("ok" if value else "")

    def _cycle_status(self, item: dict, status_btn: tk.Button) -> None:
        """Клик по кнопке состояния:  ·  ->  ✓ (работает)  ->  ✗ (не работает)  ->  ·."""
        order = ["", "ok", "fail"]
        current = self._status_of(item)
        new_status = order[(order.index(current) + 1) % len(order)]
        if new_status:
            self.progress[self._key(item)] = new_status
        else:
            self.progress.pop(self._key(item), None)
        _save_progress(self.progress)
        status_btn.config(text=STATUS_SYMBOLS[new_status], fg=STATUS_COLORS[new_status])
        for var, it in self._vars:
            if it is item:
                var.set(new_status != "")
        self._refresh_progress()

    def _send_test_phrase(self, item: dict) -> None:
        """Отправить фразу пункта как ручной ввод пользователя (мимо микрофона).

        Фраза пишется в cache/manual_commands.json — её опрашивает main loop
        start.py, как обычный ручной ввод из dev-инструментов.

        FIX (баг «отправляет название строки вместо команды»): колонка фразы в
        SMOKE_CHECK.md содержит варианты в кавычках и описания («Говорить
        негромко», «прыгни» / «попрыгай»). Берём первую кавыченную фразу и
        чистим её; если кавыченной фразы нет — пункт описательный, ничего
        не отправляем.
        """
        raw = str(item.get("phrase") or "").strip()
        quoted = re.findall(r"[«\"]([^»\"]+)[»\"]", raw)
        if quoted:
            phrase = quoted[0].strip()
        elif raw and re.search(r"[а-яёa-z]", raw) and "/" not in raw and "(" not in raw:
            phrase = raw
        else:
            self.progress_var.set("В этом пункте нет голосовой фразы — проверь вручную.")
            return
        if not phrase:
            return
        try:
            from app import manual_control
            manual_control.enqueue(phrase)
            manual_control.remember_last(phrase)
        except Exception:
            pass
        self.progress_var.set(f"Отправлено: «{phrase[:40]}»")

    def _reset(self):
        self.progress = {}
        _save_progress(self.progress)
        for var, _ in self._vars:
            var.set(False)
        for status_btn, item in self._status_buttons:
            status_btn.config(text=STATUS_SYMBOLS[""], fg=STATUS_COLORS[""])
        self._refresh_progress()

    def _refresh_progress(self):
        statuses = [self._status_of(item) for _, item in self._vars]
        ok = sum(1 for s in statuses if s == "ok")
        fail = sum(1 for s in statuses if s == "fail")
        self.progress_var.set(f"Проверено: {ok + fail}/{len(self.items)}   ✓ {ok}   ✗ {fail}")

    def destroy(self):
        try:
            self.unbind_all("<MouseWheel>")
        except Exception:
            pass
        super().destroy()


def open_smoke_test_window(parent: tk.Widget) -> tk.Toplevel:
    """Отдельное окно смок-теста (вызывается из подвкладки БАГИ)."""
    win = tk.Toplevel(parent)
    win.title("СМОК-ТЕСТ")
    win.configure(bg=BG)
    win.geometry("880x640")

    class _SmokeInWindow(SmokeTestScreen):
        def _make_back_button(self):
            tk.Button(self, text="[ ✕ ЗАКРЫТЬ ]", bg=BG, fg=FG, font=FONT, activebackground=DIM,
                      activeforeground=AMBER, relief="flat", cursor="hand2",
                      command=win.destroy).pack(pady=(8, 0))

    _SmokeInWindow(win, app=None).pack(fill="both", expand=True, padx=12, pady=10)
    return win
