"""Экран [ КОНСОЛЬ ]: управляемый запуск Нимфеи + живой лог с цветами.

Задачи (v6.27.0, обновлено v13.0.0):
- кнопка запуска БЕЗ отдельного консольного окна (вывод — сюда, в лог);
- кнопка выключения проекта (убивает запущенный нами процесс вместе с аватаром);
- ошибки из лога: [WARNING] — жёлтым, [ERROR] — красным;
- реплики Нимфеи ([SpeakText] source=nima*) видны отдельно от тех. шума;
- мини-статус: текущие анимация · настроение · одежда (avatar/avatar_state.json).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import tkinter as tk

from .config import AMBER, BG, DIM, FG, FONT, FONT_SM, PROJECT_ROOT, RED_ERR

LOG_PATH = PROJECT_ROOT / "logs" / "technical_context.log"
STATE_PATH = PROJECT_ROOT / "avatar" / "avatar_state.json"

# Реплика Нимфеи в логе: "... [SpeakText] source=nima:llm text='...'"
_SPEAK_RE = re.compile(r"\[SpeakText\] source=(nima\S*|vita\S*) text=(.+)$")

_OUTFIT_LABELS = {
    "Nima_standart.vrm": "Стандарт", "Nima_drees.vrm": "Платье",
    "Nima_jeens.vrm": "Джинсы", "Nima_kimono.vrm": "Кимоно",
    "Nima_maid.vrm": "Горничная", "Nima_naked.vrm": "Без одежды",
    "Nima_Sexual.vrm": "Сексуальный образ",
}

# Запущенный из debug menu процесс start.py. Общий на главное меню и КОНСОЛЬ:
# запустить можно с одного экрана, выключить — с любого.
_tracked_proc: subprocess.Popen | None = None

# env для оркестратора start.py. Меню запускает его напрямую, поэтому
# дублируем здесь переменные Ollama (урок v11: не пускать вторые модели в VRAM).
_MENU_ENV = {
    # v14.8: держим ОБЕ модели резидентно в VRAM — диалоговую gemma3:4b и
    # RAG-эмбеддер nomic-embed-text. Раньше стоял лимит =1, и Ollama по кругу
    # выгружала одну ради другой (см. logs/ollama_serve.log: 17 перезапусков
    # llama-server, VRAM прыгает 4.4↔2.3 ГБ) — это и выглядело как «Ollama
    # падает и поднимается». Бюджет 6 ГБ: gemma3:4b q4 (+q8_0 KV) ~4 ГБ +
    # nomic (~0.3 ГБ) помещаются. Если начнёт не влезать — вернуть "1" или
    # увести эмбеддер на CPU (memory/_embed_texts → options.num_gpu=0).
    "OLLAMA_MAX_LOADED_MODELS": "2",
    "OLLAMA_NUM_PARALLEL": "1",
    # -1 = не выгружать по таймеру: модель, загруженная один раз, остаётся в
    # VRAM (иначе через 10 мин выгрузка → следующий запрос снова грузит →
    # снова своп со второй моделью).
    "OLLAMA_KEEP_ALIVE": "-1",
    # q8_0 KV-cache: вдвое меньше VRAM под контекст — помогает уместить обе
    # модели в 6 ГБ GTX 1660 Super.
    "OLLAMA_KV_CACHE_TYPE": "q8_0",

    # === ЗВУК (v14.8): развязка «Нима не слышит саму себя» + вывод в созвон ===
    # Железо: микрофон = DEXP U700, наушники = Динамики (High Definition Audio).
    # Голос Нимы играет в VB-CABLE (НЕ в наушники) → её TTS физически не попадает
    # в loopback → она не слышит собственный голос (нет эхо-петли).
    #   • TTS_OUTPUT_DEVICE = CABLE Input     — куда играет её голос (в кабель);
    #     VoiceMeeter берёт CABLE Output (strip CABLE → A1+B1) и микрофон DEXP
    #     (strip → B1): через A1 ты слышишь Ниму в наушниках, через B1 её
    #     слышит друг в созвоне вместе с тобой.
    #   • TTS_MONITOR_DEVICE = ""             — прямой дубль в наушники ОТКЛЮЧЁН:
    #     после пересборки аудио-стека (смены версий VoiceMeeter) физические
    #     endpoint'ы (Динамики HDA, DEXP) «зомби» — Windows их показывает
    #     активными, но stream не открывается (-9985/-9996) ни в одном API,
    #     работает только путь Voicemeeter через ядро. Дубль всё равно избыточен:
    #     CABLE Output уже коммутируется в Voicemeeter на A1 (твои уши) + B1
    #     (созвон), так что голос доходит до обоих без второго stream'а.
    #   • STT_LOOPBACK_DEVICE = Voicemeeter Input — Нима слушает всё, что играют
    #     приложения (вывод по умолчанию), но НЕ свой голос (он в CABLE).
    "NIMA_TTS_OUTPUT_DEVICE": "CABLE Input",
    "NIMA_TTS_MONITOR_DEVICE": "",
    # Микрофон: слушать DEXP U700 НАПРЯМУЮ, а не устройство ввода по умолчанию
    # (по умолчанию стоит Voicemeeter Out B1 — микс «ты + Нима» для созвона;
    # если Нима слушала бы его, она слышала бы саму себя / молчание).
    "NIMA_STT_MIC_DEVICE": "DEXP U700",
    # Микрофон DEXP очень тихий (живой замер v14.8.2: фон ~0.0009 RMS, речь
    # в пике ~0.0026 при пороге 0.004 — VAD почти не срабатывал, «Нима глухая»).
    # Усиление x3 в коде до VAD/whisper (Windows-настройки созвона не трогаем),
    # адаптивный порог ослаблен до 2x фона (стандартные 3x съедали речь).
    "NIMA_STT_MIC_GAIN": "3",
    "NIMA_STT_RMS_NOISE_MULT": "2",
    # Loopback: слушаем «Voicemeeter Input» — это вывод по умолчанию в Windows,
    # куда играют ВСЕ приложения (игра, браузер, голос друга из созвона).
    # Голос Нимы туда не попадает (TTS играет в CABLE Input напрямую) → она
    # слышит всё, кроме себя. Раньше слушали «Динамики (HDA)» (выход A1), но
    # живой тест v14.8.1 показал: после смены версий VoiceMeeter этот endpoint
    # навсегда перестаёт открываться (-9996 Invalid device), а loopback
    # «Voicemeeter Input» стабильно работает. stt_module при трёх неудачах
    # открытия сам падает на loopback по умолчанию (= этот же устройство).
    "NIMA_STT_LOOPBACK_DEVICE": "Voicemeeter Input",
}


def _venv_python() -> str:
    """Путь к интерпретатору venv проекта (fallback — системный python)."""
    candidate = PROJECT_ROOT / "myenv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.exists() else "python"


def _ensure_ollama_serving() -> None:
    """Поднимает `ollama serve` в фоне, если он ещё не запущен.

    Раньше это делал start.bat через `curl`-опрос, но в безоконном cmd
    (CREATE_NO_WINDOW, stdin=DEVNULL) curl и перенаправления вели себя
    нестабильно, и батник зависал в цикле ожидания Ollama — до python
    start.py дело не доходило, а меню уже писало «запущено». Теперь Ollama
    поднимаем здесь напрямую, без зависимости от curl.
    """
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # уже запущен?
    try:
        running = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV"],
            capture_output=True, timeout=8, creationflags=no_window,
            # ru-Windows отдаёт вывод в cp866: text=True с utf-8 ронял
            # reader-поток декодированием. errors="replace" => str без падений.
            errors="replace",
        ).stdout
        if "ollama.exe" in running.lower():
            return
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=no_window,
        )
    except Exception:
        # ollama может быть не в PATH — старт всё равно попробуем,
        # AIModule/vision честно залогируют недоступность Ollama.
        pass


def _instance_lock_free() -> bool:
    """Свободен ли lock единственного экземпляра (start.py блокирует байт 0
    пустого logs/nima_instance.lock всё время работы)."""
    import msvcrt
    lock_path = PROJECT_ROOT / "logs" / "nima_instance.lock"
    if not lock_path.exists():
        return True
    try:
        with open(lock_path, "r+") as handle:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return True
    except OSError:
        return False


def launch_project() -> str:
    """Запускает оркестратор start.py напрямую (без консольного окна).

    Сознательно НЕ через cmd /c start.bat: под CREATE_NO_WINDOW c
    stdin=DEVNULL батник ненадёжен (timeout без stdin падает, а после
    activate.bat cmd уходил в 9009 «команда не найдена»). Вместо этого
    запускаем venv-python напрямую и передаём env из start.bat (_MENU_ENV).

    Возвращает статус с реальной проверкой: процесс должен прожить хотя бы
    пару секунд. Если он мгновенно умирает (например, падение импорта),
    сообщаем об ошибке, а не врём «запущено».
    """
    global _tracked_proc
    if _tracked_proc and _tracked_proc.poll() is None:
        return f"Уже запущено из меню (PID {_tracked_proc.pid})"
    if not _instance_lock_free():
        return ("Нимфея уже запущена (не из меню) — сначала ВЫКЛЮЧИТЬ ПРОЕКТ. "
                "Второй запуск = два окна аватара и конфликт аудио.")
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _ensure_ollama_serving()
    env = os.environ.copy()
    for key, value in _MENU_ENV.items():
        env.setdefault(key, value)
    # NIMA_-переменные меню — приоритетнее унаследованных из пользовательского
    # окружения (реестр): там остаётся устаревший NIMA_TTS_MONITOR_DEVICE=
    # "High Definition Audio" на физический endpoint, мёртвый на уровне
    # Windows, — setdefault его не перебивает и ТТС спамит -9985.
    for key, value in _MENU_ENV.items():
        if key.startswith("NIMA_"):
            env[key] = value
    try:
        _tracked_proc = subprocess.Popen(
            [_venv_python(), "start.py"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creationflags,
        )
    except Exception as e:
        _tracked_proc = None
        return f"Не удалось запустить: {e}"

    # Проверяем, что процесс не упал сразу (импорт/инициализация).
    time.sleep(2.5)
    code = _tracked_proc.poll()
    if code is not None:
        _tracked_proc = None
        return (f"Процесс завершился сразу (код {code}). "
                f"Смотри logs/technical_context.log — вероятно падение при инициализации.")
    return f"Запущено из меню (PID {_tracked_proc.pid}) — вывод в логе technical_context.log"


def _find_start_pids() -> list[int]:
    """PID всех процессов, чья командная строка содержит start.py (запуск не из меню)."""
    try:
        out = subprocess.run(
            ["wmic", "process", "where", "CommandLine like '%start.py%'", "get", "ProcessId"],
            capture_output=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            # wmic на ru-Windows пишет в cp866: utf-8 text=True падал декодированием
            # и stop_project молча «не находил» работающий проект.
            errors="replace",  # => stdout уже str, нераспознанные байты заменены
        ).stdout
    except Exception:
        return []
    return [int(token) for token in out.split() if token.isdigit()]


def _unload_ollama_models() -> str:
    """Просит Ollama выгрузить модели из VRAM (keep_alive: 0).

    Модели закреплены резидентно (ai_module keep_alive="-1m", ambient
    moondream — тоже), поэтому простой taskkill start.py их не освобождает:
    ollama.exe — отдельный процесс и продолжает держать видеопамять.
    """
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=4) as resp:
            loaded = json.loads(resp.read().decode("utf-8")).get("models", [])
    except Exception:
        return ""  # Ollama не запущен — выгружать нечего
    for entry in loaded:
        model = entry.get("model") or entry.get("name")
        if not model:
            continue
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": model, "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception:
            pass
    names = ", ".join(e.get("model") or e.get("name") or "?" for e in loaded)
    return f"; выгружено из VRAM: {names}" if names else ""


def stop_project(unload_vram: bool = True) -> str:
    """Останавливает проект: сначала отслеживаемое дерево, затем любые start.py.

    start.py убивается принудительно (taskkill /F) и не успевает выгрузить
    модели сам, поэтому после — просим Ollama освободить VRAM.
    """
    global _tracked_proc
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    stopped = []
    if _tracked_proc and _tracked_proc.poll() is None:
        pid = _tracked_proc.pid
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=no_window)
        stopped.append(str(pid))
    _tracked_proc = None
    for pid in _find_start_pids():
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=no_window)
        stopped.append(str(pid))
    if not stopped:
        return "Запущенный проект не найден"
    unload_msg = _unload_ollama_models() if unload_vram else ""
    return f"Проект остановлен (PID: {', '.join(stopped)}){unload_msg}"


def restart_project() -> str:
    """Быстрый рестарт: стоп → короткая пауза → старт. Возвращает статус."""
    stop_msg = stop_project(unload_vram=False)
    time.sleep(1.5)  # даём процессам и Electron-аватару полностью закрыться
    launch_msg = launch_project()
    return f"РЕСТАРТ: {stop_msg}; {launch_msg}"



class RuntimeConsoleScreen(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app
        self._proc = None  # совместимость; реальный процесс — в модуле (launch_project/stop_project)
        self._follow_offset = self._current_log_size()
        self._build()
        self._poll()

    # --- интерфейс ---

    def _build(self):
        tk.Label(self, text="╔══════════════════════════════════════╗", bg=BG, fg=FG, font=FONT).pack()
        tk.Label(self, text="║  КОНСОЛЬ НИМФЕИ                     ║", bg=BG, fg=AMBER, font=FONT).pack()
        tk.Label(self, text="╚══════════════════════════════════════╝", bg=BG, fg=FG, font=FONT).pack(pady=(0, 6))

        btns = tk.Frame(self, bg=BG); btns.pack(pady=(0, 4))
        self.btn_launch = tk.Button(btns, text="[ ▶ ЗАПУСК (без окна консоли) ]", bg=BG, fg=FG, font=FONT,
                                    activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                                    command=self._launch)
        self.btn_launch.pack(side="left", padx=4)
        self.btn_stop = tk.Button(btns, text="[ ■ ВЫКЛЮЧИТЬ ПРОЕКТ ]", bg=BG, fg=RED_ERR, font=FONT,
                                  activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                                  command=self._stop)
        self.btn_stop.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Проект не запущен из меню")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM).pack()

        self.only_vita_var = tk.BooleanVar(value=False)
        tk.Checkbutton(self, text="только реплики Нимфеи", variable=self.only_vita_var, bg=BG, fg=FG,
                       selectcolor=DIM, activebackground=BG, activeforeground=AMBER, font=FONT_SM,
                       command=self._clear_view).pack()

        text_frame = tk.Frame(self, bg=BG); text_frame.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(text_frame, orient="vertical")
        self.log_text = tk.Text(text_frame, bg="#101010", fg=FG, font=FONT_SM, wrap="word",
                                state="disabled", yscrollcommand=vsb.set)
        vsb.config(command=self.log_text.yview)
        self.log_text.grid(row=0, column=0, sticky="nsew"); vsb.grid(row=0, column=1, sticky="ns")
        text_frame.rowconfigure(0, weight=1); text_frame.columnconfigure(0, weight=1)
        self.log_text.tag_config("err", foreground=RED_ERR)
        self.log_text.tag_config("warn", foreground=AMBER)
        self.log_text.tag_config("vita", foreground="#66ccff")

        self.avatar_status_var = tk.StringVar(value="анимация: —  •  настроение: —  •  одежда: —")
        tk.Label(self, textvariable=self.avatar_status_var, bg=DIM, fg=FG, font=FONT_SM).pack(fill="x")

        tk.Button(self, text="[ ← НАЗАД ]", bg=BG, fg=FG, font=FONT, activebackground=DIM,
                  activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self._close).pack(pady=(6, 0))

    # --- запуск/остановка ---

    def _launch(self):
        self.status_var.set(launch_project())

    def _stop(self):
        self.status_var.set(stop_project())

    # --- лог ---

    def _current_log_size(self) -> int:
        try:
            return LOG_PATH.stat().st_size
        except OSError:
            return 0

    def _new_lines(self) -> list[str]:
        size = self._current_log_size()
        if size <= self._follow_offset:
            if size < self._follow_offset:
                self._follow_offset = size  # лог перезаписали
            return []
        try:
            with LOG_PATH.open("rb") as f:
                f.seek(self._follow_offset)
                chunk = f.read(size - self._follow_offset)
        except OSError:
            return []
        self._follow_offset = size
        return chunk.decode("utf-8", errors="replace").splitlines()

    def _append_line(self, line: str):
        match = _SPEAK_RE.search(line)
        is_vita = bool(match and (match.group(1).startswith("nima") or match.group(1).startswith("vita")))
        if self.only_vita_var.get() and not is_vita:
            return
        self.log_text.config(state="normal")
        if "[ERROR]" in line:
            self.log_text.insert(tk.END, line + "\n", "err")
        elif "[WARNING]" in line:
            self.log_text.insert(tk.END, line + "\n", "warn")
        elif is_vita:
            self.log_text.insert(tk.END, line + "\n", "vita")
        else:
            self.log_text.insert(tk.END, line + "\n")
        self.log_text.config(state="disabled")
        self.log_text.see(tk.END)

    def _clear_view(self):
        self.log_text.config(state="normal"); self.log_text.delete("1.0", tk.END)
        self.log_text.config(state="disabled")

    # --- статус аватара ---

    def _refresh_avatar_status(self):
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        outfit = str(state.get("current_outfit") or "Nima_standart.vrm")
        self.avatar_status_var.set(
            f"анимация: {state.get('action', '—')}  •  настроение: {state.get('mood', '—')}"
            f"  •  одежда: {_OUTFIT_LABELS.get(outfit, outfit)}"
        )

    def _poll(self):
        for line in self._new_lines():
            self._append_line(line)
        self._refresh_avatar_status()
        if _tracked_proc and _tracked_proc.poll() is not None:
            self.status_var.set(f"Процесс завершился (код {_tracked_proc.returncode})")
        if self.winfo_exists():
            self._poll_timer = self.after(1000, self._poll)

    def _close(self):
        try:
            self.after_cancel(self._poll_timer)
        except Exception:
            pass
        self.app.show_main_menu()
