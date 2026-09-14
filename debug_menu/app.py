import tkinter as tk

from .ui import MainMenuApp, strip_window_frame


def _close_avatar_window():
    """Закрывает окно аватара, если оно было открыто из debug menu.

    Экран «Песочница» поднимает Electron-окно аватара через
    visual_core.launch_avatar. При закрытии меню это окно должно закрываться
    вместе с ним, а не «висеть». Всё обёрнуто в try/except, чтобы отсутствие
    модуля/процесса не мешало закрытию Tk.
    """
    try:
        from avatar.bridge import stop_avatar
        stop_avatar()
    except Exception:
        pass


def _strip_window_frame(root):
    # Реализация переехала в ui.py (strip_window_frame): её нужен повторять
    # после каждого withdraw/deiconify, не только на старте.
    strip_window_frame(root)


def main():
    root = tk.Tk()
    root.geometry("760x760")
    # Без системной панели заголовка, но С кнопкой на панели задач:
    # окно перетаскивается мышью за фон главного меню (см. _on_fan_drag в ui.py).
    _strip_window_frame(root)
    app = MainMenuApp(root)

    # Главное меню рисует Electron-кольцо (debug_menu/ring/): прозрачное окно
    # без каких-либо рамок, на экране только два кольца. Tk остаётся «мозгом»
    # и держит экраны подменю; если Electron недоступен — fallback на Tk-меню.
    try:
        from .ring_bridge import start_ring
        app.ring = start_ring(app)
    except Exception:
        app.ring = None
    if app.ring:
        root.withdraw()

        def _pump_ring():
            """Забираем действия из Electron-кольца в главном потоке Tk."""
            import queue as _q
            actions = []
            try:
                while True:
                    actions.append(app.ring.actions.get_nowait())
            except _q.Empty:
                pass
            for action in actions:
                app.activate_action(action)
            if not root.winfo_exists():
                return
            root.after(100, _pump_ring)

        root.after(100, _pump_ring)

    def _on_close():
        if app.ring:
            app.ring.shutdown()
        _close_avatar_window()
        try:
            root.destroy()
        except Exception:
            pass

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()
    # выход через пункт EXIT минует _on_close — гасим Electron здесь
    if app.ring:
        app.ring.shutdown()
    return app


if __name__ == "__main__":
    main()
