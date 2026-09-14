"""Мост Python → Electron-аватар.

Единственный способ управления аватаром из рантайма: файл состояния
avatar/avatar_state.json, который main.cjs Electron-приложения читает через
fs.watch. Никаких сокетов — просто и надёжно (подход v11, себя оправдал).

Использование:
    avatar = AvatarBridge(); avatar.start()
    avatar.command_avatar(mood="joy", outfit="Nima_maid.vrm")
    avatar.set_mouth(0.6)
    avatar.stop()
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time

from core.config import (AVATAR_ALWAYS_ON_TOP, AVATAR_DIR, AVATAR_STATE_PATH,
                         LAST_OUTFIT_PATH, VRM_DIR)

log = logging.getLogger("avatar")

OUTFITS = [
    "Nima_standart.vrm", "Nima_drees.vrm", "Nima_jeens.vrm",
    "Nima_kimono.vrm", "Nima_maid.vrm", "Nima_naked.vrm", "Nima_Sexual.vrm",
]


def _saved_outfit() -> str:
    """Последняя одежда с прошлого запуска (debug menu и рантайм пишут в один файл)."""
    try:
        name = LAST_OUTFIT_PATH.read_text(encoding="utf-8").strip()
        return name if name in OUTFITS else "Nima_standart.vrm"
    except OSError:
        return "Nima_standart.vrm"


def _remember_outfit(name: str) -> None:
    try:
        LAST_OUTFIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_OUTFIT_PATH.write_text(name, encoding="utf-8")
    except OSError as exc:
        log.error("[ERROR] запись last_outfit.txt: %s", exc)


_DEFAULT_STATE = {
    "mood": "normal",
    "action": "idle",
    "current_outfit": _saved_outfit(),
    "speaking": False,
    "mouth": 0.0,
}


class AvatarBridge:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = dict(_DEFAULT_STATE)
        self._last_write = 0.0
        self._seq = 0                     # каждая команда с action = новый номер
        self._sub_seq = 0                 # субтитры: новый номер = новая анимация
        self._proc: subprocess.Popen | None = None
        self._write_state()

    # --- запуск/остановка Electron ---
    def start(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        electron = AVATAR_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
        if not electron.exists():
            log.error("[ERROR] Electron не установлен: %s не найден "
                      "(cd avatar && npm install)", electron)
            return False
        env = os.environ.copy()
        env["NIMA_VRM_DIR"] = str(VRM_DIR)
        env["NIMA_STATE_PATH"] = str(AVATAR_STATE_PATH)
        env["NIMA_ALWAYS_ON_TOP"] = "1" if AVATAR_ALWAYS_ON_TOP else "0"
        try:
            self._proc = subprocess.Popen(
                [str(electron), "."], cwd=str(AVATAR_DIR), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log.info("Аватар запущен (PID %d)", self._proc.pid)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] Electron не запустился: %s", exc)
            return False

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/PID", str(self._proc.pid), "/T", "/F"],
                               capture_output=True)
        self._proc = None

    # --- состояние ---
    def _write_state(self) -> None:
        with self._lock:
            try:
                AVATAR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = AVATAR_STATE_PATH.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
                # Electron читает файл по fs.watch: если он открыл его в момент
                # замены, os.replace получает WinError 5 — повторяем (урок v14.7:
                # без ретраев терялись настроения/субтитры по разу за вечер)
                for attempt in range(3):
                    try:
                        os.replace(tmp, AVATAR_STATE_PATH)
                        self._last_write = time.time()
                        return
                    except PermissionError:
                        if attempt == 2:
                            raise
                        time.sleep(0.02)
            except OSError as exc:
                log.error("[ERROR] запись avatar_state.json: %s", exc)

    # Ключи, которые процесс-писатель «делит» с другими процессами (pipeline,
    # debug menu — по экземпляру AvatarBridge на процесс). Раньше каждый писал
    # свой снимок _state ЦЕЛИКОМ и затирал чужое: клик «танец» в debug menu
    # убивался следующим же mouth-обновлением конвейера (action откатывался в
    # 'idle', рендерер гасил анимацию — «просто стоит и дёргает рукой»).
    _SHARED_KEYS = ("action", "seq", "mood", "current_outfit", "subtitle")

    def _set(self, **kwargs) -> None:
        changed = False
        with self._lock:
            # Чужие значения разделяемых ключей подтягиваем из файла: файл —
            # источник истины для всего, что ЭТОТ вызов не задаёт явно.
            explicit = set(kwargs)
            try:
                disk = json.loads(AVATAR_STATE_PATH.read_text(encoding="utf-8"))
                for key in self._SHARED_KEYS:
                    if key not in explicit and key in disk and self._state.get(key) != disk[key]:
                        self._state[key] = disk[key]
                        changed = True
            except (OSError, ValueError):
                pass  # файла может не быть — пишем как раньше
            for key, value in kwargs.items():
                if self._state.get(key) != value:
                    self._state[key] = value
                    changed = True
        # mouth меняется 10 раз в секунду — не молотим файлом чаще 20 Гц
        if changed and (time.time() - self._last_write > 0.05 or "mouth" not in kwargs):
            self._write_state()

    # --- публичный API ---
    def command_avatar(self, mood: str | None = None, action: str | None = None,
                       outfit: str | None = None, speaking: bool | None = None) -> None:
        kwargs = {}
        if mood is not None:
            kwargs["mood"] = mood
        if action is not None:
            kwargs["action"] = action
            # seq заставляет рендерер переиграть даже ту же самую анимацию
            self._seq += 1
            kwargs["seq"] = self._seq
        if outfit is not None:
            if outfit in OUTFITS:
                kwargs["current_outfit"] = outfit
                _remember_outfit(outfit)  # переживает перезапуск окна и рантайма
            else:
                log.warning("[WARNING] неизвестный образ %s — пропущен", outfit)
        if speaking is not None:
            kwargs["speaking"] = speaking
        if kwargs:
            self._set(**kwargs)

    def set_mouth(self, value: float) -> None:
        self._set(mouth=round(max(0.0, min(1.0, float(value))), 3), speaking=value > 0)

    def set_subtitle(self, text: str, color: str = "", name: str = "") -> None:
        """Субтитры в окне аватара (рендерер играет караоке-анимацию:
        появление слева-направо → пауза → стирание слева-направо).
        color — цвет обводки; name — префикс «имя:» (для чужой речи).
        text "" — спрятать текущий субтитр."""
        self._sub_seq += 1
        self._set(subtitle={"text": str(text or ""), "color": str(color or ""),
                            "name": str(name or ""), "seq": self._sub_seq})

    def is_running(self) -> bool:
        return bool(self._proc and self._proc.poll() is None)

    @property
    def current_outfit(self) -> str:
        return self._state.get("current_outfit", "Nima_standart.vrm")


# --- совместимый API для debug menu (песочница, app.py) ---------------------
# Общий singleton-мост: песочница и рантайм могут существовать независимо,
# но если оба запущены — окно одно.

_shared: AvatarBridge | None = None


def _get_shared() -> AvatarBridge:
    global _shared
    if _shared is None:
        _shared = AvatarBridge()
    return _shared


def command_avatar(mood: str | None = None, action: str | None = None,
                   speaking: bool | None = None) -> None:
    # БЕЗ автозапуска окна (v14.8.16): каждый процесс со своим мостом при
    # таком коде плодил собственное Electron-окно (новое окно = перегрузка
    # модели = «анимация не запускается»). Окно создаёт ТОЛЬКО launch_avatar.
    bridge = _get_shared()
    bridge.command_avatar(mood=mood, action=action, speaking=speaking)


def jump() -> None:
    """Прыжок (VRMA_02)."""
    command_avatar(action="jump")


def switch_outfit(fname: str) -> bool:
    if not (VRM_DIR / fname).exists():
        return False
    bridge = _get_shared()
    bridge.command_avatar(outfit=fname)
    return True


def launch_avatar(mood: str | None = None, action: str | None = None) -> None:
    bridge = _get_shared()
    bridge.start()
    bridge.command_avatar(mood=mood, action=action)


def stop_avatar() -> None:
    global _shared
    if _shared:
        _shared.stop()
        _shared = None
