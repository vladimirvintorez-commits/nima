"""Вкладка «ОШИБКИ» в debug menu: журнал кодов ошибок + расшифровка.

Источник данных — core/error_registry.py:
- журнал logs/error_journal.jsonl (пишется автоматически из TechnicalLogger.error
  при коде [E-XXX-NNN] в сообщении);
- каталог ERROR_CODES: расшифровка «что сломалось» и подсказка «где искать».
"""
from __future__ import annotations

import tkinter as tk

from .config import BG, FG, AMBER, DIM, RED_ERR, FONT, FONT_SM
from .ui import BaseScreen


class ErrorViewerScreen(BaseScreen):
    """Экран [ ОШИБКИ ]: последние коды из журнала + расшифровка кода."""

    def __init__(self, master, app):
        super().__init__(master, app)
        self._init_keyboard_nav()
        self._build()
        self._focus_widget(0)

    def _build(self):
        from core.error_registry import catalog, recent, stats

        self._make_header("ОШИБКИ: КОДЫ И ЖУРНАЛ")
        entries = recent(limit=100)
        agg = stats()
        tk.Label(
            self,
            text=f"Записей в журнале: {len(entries)} · уникальных кодов: {len(agg)} · журнал: logs/error_journal.jsonl",
            bg=BG, fg=DIM, font=FONT_SM,
        ).pack()

        self.text = tk.Text(self, height=18, width=86, bg="#101010", fg=FG, font=FONT_SM,
                            insertbackground=FG, relief="flat", wrap="word")
        self.text.pack(padx=8, pady=(4, 6))
        self.text.tag_configure("code", foreground=AMBER, font=FONT)
        self.text.tag_configure("err", foreground=RED_ERR)
        self.text.tag_configure("dim", foreground=DIM)

        if entries:
            for entry in entries:
                ts = entry.get("ts", "")
                code = entry.get("code", "E-UNK-000")
                message = entry.get("message", "")
                self.text.insert("end", f"{ts} ", "dim")
                self.text.insert("end", f"[{code}]", "code")
                self.text.insert("end", f" {message}\n", "err")
        else:
            self.text.insert("end", "Журнал пуст — ошибок с кодами не зафиксировано.\n", "dim")
        self.text.configure(state="disabled")

        row = tk.Frame(self, bg=BG)
        row.pack(pady=2)
        tk.Label(row, text="Код:", bg=BG, fg=FG, font=FONT_SM).pack(side="left")
        self.code_entry = tk.Entry(row, width=14, bg="#101010", fg=FG, font=FONT,
                                   insertbackground=FG, relief="flat")
        self.code_entry.pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="Введи код (например E-LLM-001) и нажми [ РАСШИФРОВКА ]")
        btn_lookup = tk.Button(row, text="[ РАСШИФРОВКА ]", bg=BG, fg=FG, font=FONT,
                               activebackground=DIM, activeforeground=AMBER,
                               relief="flat", cursor="hand2", command=self._lookup)
        btn_lookup.pack(side="left", padx=4)
        btn_catalog = tk.Button(row, text="[ КАТАЛОГ КОДОВ ]", bg=BG, fg=FG, font=FONT,
                                activebackground=DIM, activeforeground=AMBER,
                                relief="flat", cursor="hand2", command=self._show_catalog)
        btn_catalog.pack(side="left", padx=4)
        btn_clear = tk.Button(row, text="[ ОЧИСТИТЬ ЖУРНАЛ ]", bg=BG, fg=DIM, font=FONT,
                              activebackground=DIM, activeforeground=RED_ERR,
                              relief="flat", cursor="hand2", command=self._clear)
        btn_clear.pack(side="left", padx=4)

        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM,
                 wraplength=620, justify="left").pack(pady=(2, 0))
        self._make_back_button()
        self._register_focusable(self.code_entry, arrow_nav=False)
        self._register_focusable(btn_lookup, self._lookup)
        self._register_focusable(btn_catalog, self._show_catalog)
        self._register_focusable(btn_clear, self._clear)

    def _lookup(self):
        from core.error_registry import resolve
        code = " ".join(str(self.code_entry.get()).split()).upper()
        if not code:
            self.status_var.set("Пусто: введи код вида E-LLM-001")
            return
        info = resolve(code)
        unknown = info.get("code") != code
        prefix = "Код не из каталога! " if unknown else ""
        self.status_var.set(f"{prefix}{code}: {info['desc']} · ГДЕ ИСКАТЬ: {info['where']}")

    def _show_catalog(self):
        from core.error_registry import catalog
        info = catalog()
        self.status_var.set(
            "Каталог (" + str(len(info)) + " кодов): "
            + " · ".join(f"{c} — {i['desc'].split('(')[0].strip().rstrip('.')}" for c, i in sorted(info.items()))
        )

    def _clear(self):
        from core.error_registry import clear_journal
        removed = clear_journal()
        self.status_var.set(f"Журнал очищен: стёрто {removed} записей.")
