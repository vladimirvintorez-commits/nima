"""АВТОТЕСТ: все офлайн-механики проверяются одна за другой по списку.

Кнопка в смок-тесте запускает полный набор детерминированных проверок
(debug_menu/full_test.py::TESTS) в фоновом потоке: каждый пункт появляется
в списке по мере прогона со статусом ✓/✗. Голосовые пункты смок-теста
(слух, TTS, аватар) автоматизировать нельзя — их отмечает человек по списку.
"""
from __future__ import annotations

import threading
import tkinter as tk

from .config import AMBER, BG, DIM, FG, FONT, FONT_SM, RED_ERR


def run_auto_test(progress_cb, done_cb) -> None:
    """Прогоняет TESTS по одному; progress_cb(i, total, категория, имя, ok, detail)."""
    from .full_test import TESTS

    def _worker():
        total = len(TESTS)
        results = []
        for i, (category, name, fn) in enumerate(TESTS, 1):
            try:
                fn()
                ok, detail = True, ""
            except Exception as exc:  # noqa: BLE001 — тест должен пережить любую ошибку
                ok, detail = False, str(exc)[:300]
            results.append((category, name, ok, detail))
            try:
                progress_cb(i, total, category, name, ok, detail)
            except Exception:
                pass
        try:
            done_cb(results)
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def open_auto_test_window(parent: tk.Widget) -> tk.Toplevel:
    """Отдельное окно АВТОТЕСТА (вызывается из смок-теста)."""
    win = tk.Toplevel(parent)
    win.title("АВТОТЕСТ МЕХАНИК")
    win.configure(bg=BG)
    win.geometry("880x640")

    status_var = tk.StringVar(value="Запуск…")
    tk.Label(win, text="АВТОТЕСТ: ВСЕ МЕХАНИКИ ПО СПИСКУ", bg=BG, fg=AMBER,
             font=("Courier New", 13, "bold")).pack(pady=(10, 2))
    tk.Label(win, textvariable=status_var, bg=BG, fg=FG, font=FONT_SM).pack()
    frame = tk.Frame(win, bg=BG)
    frame.pack(fill="both", expand=True, padx=12, pady=8)
    sb = tk.Scrollbar(frame, orient="vertical")
    listbox = tk.Listbox(frame, bg=BG, fg=FG, font=("Courier New", 9),
                         yscrollcommand=sb.set, relief="flat", borderwidth=0)
    sb.config(command=listbox.yview)
    listbox.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")

    def on_progress(i, total, category, name, ok, detail):
        mark = "✓" if ok else "✗"
        color = FG if ok else RED_ERR
        line = f"[{mark}] {i}/{total}  {category}: {name}" + (f" — {detail}" if detail else "")
        listbox.insert(tk.END, line)
        listbox.itemconfig(tk.END, {"fg": color})
        listbox.see(tk.END)
        status_var.set(f"Прогон {i}/{total}…")

    def on_done(results):
        ok_n = sum(1 for r in results if r[2])
        fail_n = len(results) - ok_n
        summary = f"Готово: ✓ {ok_n}   ✗ {fail_n}. Голосовое (STT/TTS/аватар) проверяй вручную по списку смок-теста."
        status_var.set(summary)
        tk.Button(win, text="[ ✕ ЗАКРЫТЬ ]", bg=BG, fg=FG, font=FONT, activebackground=DIM,
                  activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=win.destroy).pack(pady=(0, 10))

    run_auto_test(on_progress, on_done)
    return win
