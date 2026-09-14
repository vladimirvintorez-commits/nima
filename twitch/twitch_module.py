"""Twitch-чат для Нимфеи-стримерши: IRC по TLS, только чтение.

Включается ТОЛЬКО если заданы переменные окружения:
    NIMA_TWITCH_CHANNEL = имя канала
    NIMA_TWITCH_TOKEN   = oauth:...  (https://twitchapps.com/tmi/, scope chat:read)

Сообщения чата приходят в колбэк on_message(nick, text) — pipeline решает,
отвечать ли. Пока модуль выключен, проект живёт как голосовой компаньон.
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading

from core.config import TWITCH_CHANNEL, TWITCH_NICK, TWITCH_TOKEN

log = logging.getLogger("twitch")

HOST, PORT = "irc.chat.twitch.tv", 6697


class TwitchModule:
    def __init__(self, on_message) -> None:
        self.on_message = on_message          # callable(nick: str, text: str)
        self.enabled = bool(TWITCH_CHANNEL and TWITCH_TOKEN)
        self._sock = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not self.enabled:
            log.info("Twitch отключён: не заданы NIMA_TWITCH_CHANNEL / NIMA_TWITCH_TOKEN")
            return
        threading.Thread(target=self._loop, name="Twitch", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass

    def _loop(self) -> None:
        try:
            raw = socket.create_connection((HOST, PORT), timeout=15)
            self._sock = ssl.create_default_context().wrap_socket(raw)
            self._sock.sendall(f"PASS {TWITCH_TOKEN}\r\n".encode())
            self._sock.sendall(f"NICK {TWITCH_NICK}\r\n".encode())
            self._sock.sendall(f"JOIN #{TWITCH_CHANNEL}\r\n".encode())
            log.info("Twitch подключён к #%s", TWITCH_CHANNEL)
        except Exception as exc:  # noqa: BLE001
            log.error("[ERROR] Twitch не подключился: %s", exc)
            return
        buffer = ""
        while not self._stop.is_set():
            try:
                chunk = self._sock.recv(4096).decode("utf-8", errors="replace")
                if not chunk:
                    break
                buffer += chunk
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    self._handle_line(line)
            except OSError:
                break
        log.info("Twitch отключён")

    def _handle_line(self, line: str) -> None:
        if line.startswith("PING"):
            try:
                self._sock.sendall(b"PONG :tmi.twitch.tv\r\n")
            except OSError:
                pass
            return
        if " PRIVMSG " not in line:
            return
        try:
            nick = line.split("!", 1)[0].lstrip(":")
            text = line.split(" PRIVMSG ", 1)[1].split(" :", 1)[1]
            self.on_message(nick, text.strip())
        except Exception:  # noqa: BLE001
            log.exception("[ERROR] не разобрать строку чата")
