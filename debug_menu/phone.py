"""Экран [ ТЕЛЕФОН ]: адрес моста «Нимфея на телефоне» и его статус.

Показывает ссылку вида http://<ip>:<порт>/?t=<токен> — её вводишь в браузер
телефона или в APK. Сервер живёт в пакете remote/ и стартует вместе с start.py
(env VITA_REMOTE=0 выключает); этот экран читает его статус-файл
cache/remote_bridge.json (import start из debug-меню делать нельзя — тяжёлая
инициализация).
"""
from __future__ import annotations

import json
import socket
import subprocess
import tkinter as tk
from tkinter import messagebox

from .config import AMBER, BG, DIM, FG, FONT, FONT_SM, PROJECT_ROOT

STATUS_PATH = PROJECT_ROOT / "cache" / "remote_bridge.json"
DOCS_PATH = PROJECT_ROOT / "android" / "README.md"


def _candidate_ips() -> list:
    """Все IPv4 ПК: сначала локальная сеть, потом Tailscale (100.x.y.z)."""
    ips = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.255.255", 1))
        ips.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    lan = [ip for ip in ips if not ip.startswith("100.")]
    tailscale = [ip for ip in ips if ip.startswith("100.")]
    return lan + tailscale


def _local_ip() -> str:
    """Основной IP ПК в локальной сети."""
    ips = _candidate_ips()
    return ips[0] if ips else "127.0.0.1"


def _tailscale_ip() -> str:
    """IP в сети Tailscale, если установлен (иначе пустая строка)."""
    for ip in _candidate_ips():
        if ip.startswith("100."):
            return ip
    return ""


def load_bridge_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class PhoneScreen(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app
        self._build()
        self._make_back_button()

    def _make_back_button(self):
        tk.Button(self, text="[ ← НАЗАД ]", bg=BG, fg=FG, font=FONT, activebackground=DIM,
                  activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self.app.show_main_menu).pack(pady=(8, 0))

    def _make_header(self, title):
        # Локальная копия BaseScreen._make_header: ui.py инстанцирует этот экран,
        # прямой импорт оттуда дал бы циклический импорт.
        tk.Label(self, text="╔══════════════════════════════════════╗", bg=BG, fg=FG, font=FONT).pack()
        tk.Label(self, text=f"║  {title:<36}║", bg=BG, fg=AMBER, font=FONT).pack()
        tk.Label(self, text="╚══════════════════════════════════════╝", bg=BG, fg=FG, font=FONT).pack(pady=(0, 8))

    def _build(self):
        self._make_header("ТЕЛЕФОН: НИМФЕЯ В КАРМАНЕ")
        tk.Label(self, text="В одной Wi-Fi сети — первый адрес. Из любой другой сети\n"
                            "(мобильный интернет) — адрес через Tailscale ниже.",
                 bg=BG, fg=FG, font=FONT_SM, justify="left").pack(pady=(0, 8))

        status = load_bridge_status()
        port = int(status.get("port", 8765))
        token = str(status.get("token", ""))
        scheme = "https" if status.get("tls") else "http"
        running = bool(status.get("running"))
        clients = int(status.get("clients", 0))

        if running and token:
            url = f"{scheme}://{_local_ip()}:{port}/?t={token}"
            state = f"Мост активен. Телефонов подключено: {clients}."
        else:
            url = f"{scheme}://{_local_ip()}:{port}/?t=<токен появится после запуска start.bat>"
            state = "Проект не запущен — мост стартует вместе с start.py."
        self._url = url

        # Tailscale: доступ с телефона из любой сети (мобильный интернет и т.п.),
        # без проброса портов. Если IP 100.x найден — показываем второй адрес.
        self._ts_url = ""
        ts_ip = _tailscale_ip()
        if ts_ip:
            suffix = f"?t={token}" if running and token else "/?t=<токен после запуска>"
            self._ts_url = f"{scheme}://{ts_ip}:{port}/{suffix}"

        tk.Label(self, text="Адрес для телефона (одна Wi-Fi сеть):", bg=BG, fg=DIM,
                 font=FONT_SM).pack()
        tk.Label(self, text=url, bg="#101010", fg=AMBER, font=FONT,
                 wraplength=620, justify="left", padx=10, pady=8).pack(pady=(2, 6), fill="x", padx=40)
        if self._ts_url:
            tk.Label(self, text="Адрес через Tailscale (работает из ЛЮБОЙ сети —\n"
                                "мобильный интернет, чужой Wi-Fi; поставить tailscale.com\n"
                                "на ПК и телефон, оба в одном аккаунте):",
                     bg=BG, fg=DIM, font=FONT_SM, justify="left").pack()
            tk.Label(self, text=self._ts_url, bg="#101010", fg="#7fd0ff", font=FONT,
                     wraplength=620, justify="left", padx=10, pady=8).pack(pady=(2, 6), fill="x", padx=40)
        tk.Label(self, text=state, bg=BG, fg=FG, font=FONT_SM).pack(pady=(0, 8))

        # Параллельный http-порт для андроида (WebView не любит самоподписанный https)
        port_plain = status.get("port_plain")
        if running and token and port_plain:
            self._android_url = f"http://{_local_ip()}:{port_plain}/?t={token}"
            tk.Label(self, text="Адрес для Android (если https-адрес не открыётся):",
                     bg=BG, fg=DIM, font=FONT_SM).pack()
            tk.Label(self, text=self._android_url, bg="#101010", fg="#9fe89f", font=FONT,
                     wraplength=620, justify="left", padx=10, pady=8).pack(pady=(2, 6), fill="x", padx=40)

        btn_row = tk.Frame(self, bg=BG)
        btn_row.pack(pady=4)
        tk.Button(btn_row, text="[ СКОПИРОВАТЬ АДРЕС ]", bg=BG, fg=AMBER, font=FONT,
                  activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self._copy).pack(side="left", padx=6)
        if self._ts_url:
            tk.Button(btn_row, text="[ СКОПИРОВАТЬ TAILSCALE ]", bg=BG, fg="#7fd0ff", font=FONT,
                      activebackground=DIM, activeforeground="#7fd0ff", relief="flat", cursor="hand2",
                      command=self._copy_ts).pack(side="left", padx=6)
        tk.Button(btn_row, text="[ ИНСТРУКЦИЯ · android/README ]", bg=BG, fg=FG, font=FONT,
                  activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self._open_docs).pack(side="left", padx=6)
        if not running:
            tk.Button(btn_row, text="[ ЗАПУСТИТЬ ПРОЕКТ ]", bg=BG, fg=FG, font=FONT,
                      activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                      command=self.app._launch_bot).pack(side="left", padx=6)

    def _copy(self):
        self.clipboard_clear()
        self.clipboard_append(self._url)
        messagebox.showinfo("Скопировано", "Адрес в буфере обмена — вставь в браузер телефона.")

    def _copy_ts(self):
        self.clipboard_clear()
        self.clipboard_append(self._ts_url)
        messagebox.showinfo("Скопировано", "Tailscale-адрес в буфере — работает из любой сети.")

    def _open_docs(self):
        try:
            subprocess.Popen(["notepad", str(DOCS_PATH)])
        except Exception:
            pass
