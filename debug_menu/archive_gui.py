"""
Small launcher for the modular Vita Debug Menu.

Keeps compatibility with debug menu.bat, which runs archive_gui.py.

Меню запускается через pythonw.exe (без консоли). Поскольку stderr консоли нет,
любой фатальный сбой запуска логируется в logs/debug_menu_crash.log и, если
получится, показывается в GUI-messagebox — иначе меню молча не открылось бы.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path


def _report_startup_failure(exc: BaseException) -> None:
    """Пишет трейсбек в файл и пытается показать его в messagebox (консоли нет)."""
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        log_path = Path(__file__).resolve().parents[2] / "logs" / "debug_menu_crash.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(details + "\n")
    except Exception:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Debug menu — ошибка запуска", details[-1500:])
        root.destroy()
    except Exception:
        pass


if __name__ == '__main__':
    try:
        # Лаунчер лежит в debug_menu/: корень проекта = родитель папки.
        _PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
        if _PROJECT_ROOT not in sys.path:
            sys.path.insert(0, _PROJECT_ROOT)

        from debug_menu.app import main

        main()
    except Exception as exc:  # noqa: BLE001 — консоли нет, ошибку надо не потерять
        _report_startup_failure(exc)
        sys.exit(1)
