"""twitch_donations — приёмник донатов: HTTP :8791 + файловый ящик.

Экран debug menu «ТЕСТ-ДОНАТ» и внешние вебхуки (StreamElements-мосты)
присылают донаты двумя способами:
  • POST http://127.0.0.1:8791/donation  {"donor","amount","currency","message"}
  • строка JSON в data/donations_inbox.jsonl (fallback, разбирается poll-ом)

Оба пути сходятся в файловый ящик — его раз в 0.2 с разбирает start.py
(pipeline.poll_donations), донаты доходят Нимфее с ближайшей паузой.
"""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from core.config import DONATIONS_HTTP_PORT, DONATIONS_INBOX_PATH

log = logging.getLogger("donations")


def inbox_write(payload: dict) -> None:
    """Добавить донат в файловый ящик (атомарной записи не требуется: append)."""
    path: Path = DONATIONS_INBOX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 (http.server API)
        if self.path != "/donation":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            inbox_write(payload)
            log.info("донат по HTTP: %s / %s", payload.get("donor"), payload.get("amount"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] донат по HTTP: %s", exc)
            self.send_error(400)

    def log_message(self, *_args) -> None:  # тихий: не мусорить в консоль
        return


def start_http_receiver() -> threading.Thread | None:
    """Поднять HTTP-приёмник донатов в daemon-потоке (None — занят порт)."""
    try:
        server = HTTPServer(("127.0.0.1", DONATIONS_HTTP_PORT), _Handler)
    except OSError as exc:
        log.info("HTTP-приёмник донатов не поднят (%s) — живёт файловый ящик", exc)
        return None
    thread = threading.Thread(target=server.serve_forever, name="DonationsHTTP", daemon=True)
    thread.start()
    log.info("HTTP-приёмник донатов: POST http://127.0.0.1:%d/donation", DONATIONS_HTTP_PORT)
    return thread
