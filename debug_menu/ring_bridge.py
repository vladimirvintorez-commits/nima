"""Мост между Electron-кольцом меню (debug_menu/ring/) и Tk-приложением.

Electron-окно рисует только два кольца и шлёт выбор пункта по локальному HTTP:
  GET  /menu   — JSON меню (категории, пункты, иконки, версия)
  GET  /state  — {visible, quit}: показывать ли кольцо / закрыть Electron
  POST /action — {"action": "<action>"} — выбор пункта меню

Порт выбирается системой и передаётся Electron через переменную окружения
RING_PORT. Видимость: пока открыт экран Tk (песочница, консоль, ...) кольцо
скрывается, при возврате в главное меню — показывается снова.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import PROJECT_ROOT, get_version

ELECTRON_EXE = PROJECT_ROOT / "avatar" / "node_modules" / "electron" / "dist" / "electron.exe"
RING_APP_DIR = PROJECT_ROOT / "debug_menu" / "ring"
RING_LOG = PROJECT_ROOT / "logs" / "ring_electron.log"


def build_menu_json() -> dict:
    # Импорт внутри функции: ring_bridge может использоваться и без Tk-части.
    from .ui import MainMenuApp
    groups = []
    flat = 0
    for header, items in MainMenuApp.GROUPS:
        name = header.strip("─ \t").strip() or "ЕЩЁ"
        groups.append({
            "name": name,
            "flat": flat,
            "items": [
                {"label": label, "action": action,
                 "icon": MainMenuApp.ICONS.get(action, "•")}
                for label, action in items
            ],
        })
        flat += len(items)
    return {"version": get_version(), "groups": groups}


class RingBridge:
    """HTTP-сервер + процесс Electron. Живёт в daemон-потоках."""

    def __init__(self, app):
        self.app = app               # MainMenuApp
        self.visible = True
        self.quit = False
        # Выбранные пункты: HTTP-поток кладёт, Tk-поток забирает (tkinter
        # нельзя дёргать из чужого потока — root.after там кидает RuntimeError).
        self.actions = queue.Queue()
        self._electron = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self.port = self._server.server_address[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    # ------------------------------------------------------------------
    def _make_handler(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, payload: dict, status: int = 200):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/menu":
                    self._send(build_menu_json())
                elif self.path == "/state":
                    self._send({"visible": bridge.visible, "quit": bridge.quit})
                else:
                    self._send({"error": "not found"}, 404)

            def do_OPTIONS(self):
                # preflight из Electron-рендерера (file://): разрешаем всё
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.end_headers()

            def do_POST(self):
                if self.path != "/action":
                    self._send({"error": "not found"}, 404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
                    self._send({"error": "bad json"}, 400)
                    return
                action = str(data.get("action", ""))
                bridge.actions.put(action)
                self._send({"ok": True})

            def log_message(self, *_args):  # тише в консоль
                pass

        return Handler

    # ------------------------------------------------------------------
    def launch_electron(self):
        if not ELECTRON_EXE.exists():
            return None
        env = dict(os.environ, RING_PORT=str(self.port))
        try:
            log = open(RING_LOG, "a", encoding="utf-8")
            self._electron = subprocess.Popen(
                [str(ELECTRON_EXE), str(RING_APP_DIR)],
                env=env, cwd=str(RING_APP_DIR),
                stdout=log, stderr=log,
            )
        except Exception:
            self._electron = None
        return self._electron

    def shutdown(self):
        self.quit = True
        if self._electron and self._electron.poll() is None:
            try:
                self._electron.terminate()
            except Exception:
                pass


def start_ring(app) -> RingBridge | None:
    """Поднять мост и Electron-окно. None — Electron не найден (fallback на Tk)."""
    if sys.platform != "win32" or not ELECTRON_EXE.exists():
        return None
    bridge = RingBridge(app)
    bridge.launch_electron()
    return bridge
