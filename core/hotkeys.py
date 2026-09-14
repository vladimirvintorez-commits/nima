"""Глобальные хоткеи (v14.7, только Windows): управление Нимфеи без debug menu.

Ctrl+Alt+D — «глянь на экран» (она посмотрит и прокомментирует вслух)
Ctrl+Alt+M — мьют/анмьют её ушей (микрофон + loopback)
Ctrl+Alt+W — режим просмотра вкл/выкл/авто (три состояния по кругу)
Ctrl+Alt+S — мгновенно замолчать

RegisterHotKey системный: работает даже когда окно игры в фокусе. Если хоткей
уже занят другим приложением — лог и пропуск (остальные работают). Тестам
отдаётся словарь ACTIONS: маппинг «клавиша → действие», а сами действия
проводит start.py через колбэки.
"""
from __future__ import annotations

import logging
import threading

from core.config import HOTKEYS_ENABLED

log = logging.getLogger("hotkeys")

# (модификаторы, виртуальная клавиша) — MOD_CONTROL | MOD_ALT
MOD_CONTROL_ALT = 0x0002 | 0x0001
_ACTIONS = {
    "D": "look",       # глянуть на экран и прокомментировать
    "M": "mute",       # мьют ушей
    "W": "watch",      # режим просмотра: авто → вкл → выкл → авто
    "S": "hush",       # мгновенно замолчать
}
WM_HOTKEY = 0x0312


def action_for(key: str) -> str:
    return _ACTIONS.get(key.upper(), "")


def all_actions() -> dict[str, str]:
    return dict(_ACTIONS)


class HotkeyThread:
    def __init__(self, on_action, *, enabled: bool = HOTKEYS_ENABLED) -> None:
        self.on_action = on_action          # callable(action: str)
        self.enabled = enabled
        self.registered: list[str] = []
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            log.info("хоткеи выключены (NIMA_HOTKEYS=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="Hotkeys", daemon=True)
        self._thread.start()

    def stop(self) -> None:   # поток-демон; ничего не требуется
        pass

    def _loop(self) -> None:  # noqa: C901 — плоский ctypes-цикл
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        ids: dict[int, str] = {}
        next_id = 1
        for key, action in _ACTIONS.items():
            vk = ord(key.upper())
            if user32.RegisterHotKey(None, next_id, MOD_CONTROL_ALT, vk):
                ids[next_id] = action
                self.registered.append(f"Ctrl+Alt+{key.upper()}")
            else:
                log.warning("[WARNING] хоткей Ctrl+Alt+%s занят другим приложением",
                            key.upper())
            next_id += 1
        if not ids:
            log.info("хоткеи: ни один не зарегистрирован")
            return
        log.info("хоткеи: %s", ", ".join(self.registered))

        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in ids:
                action = ids[msg.wParam]
                try:
                    self.on_action(action)
                except Exception:  # noqa: BLE001
                    log.exception("[ERROR] действие хоткея %s упало", action)
        for hid in ids:
            user32.UnregisterHotKey(None, hid)
