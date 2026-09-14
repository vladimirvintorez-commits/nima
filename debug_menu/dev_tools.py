"""Экраны категории [ ИНСТРУМЕНТЫ ] debug menu.

Возвращены из версии Vita v6.29.0 («инструменты разработчика»):
    - ТРАССА ЗАПРОСА / ТРАССА ОЗВУЧКИ — цепочка этапов пайплайна из
      logs/pipeline_trace.jsonl (модуль корня pipeline_trace.py);
    - ПРОМПТ — просмотр промпта, собираемого для последней реплики;
    - РУЧНОЙ ВВОД — файловая очередь cache/manual_commands.json
      (модуль корня manual_control.py), опрашиваемая main loop;
    - БАНВОРДЫ — редактор data/speech_banwords.txt (маскируются в speak_text);
    - ИСТОРИЯ ДИАЛОГОВ — кто что сказал/ответила, из memory.json.

Все экраны best-effort: отсутствие бэкенд-модуля/файла не роняет меню, а
показывает мягкое сообщение. Импорт корневых модулей (pipeline_trace,
manual_control) делается лениво, чтобы debug menu открывалось даже без них.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
import tkinter as tk

from .config import AMBER, BG, DIM, FG, FONT, FONT_LG, FONT_SM, PROJECT_ROOT, RED_ERR

# Корень проекта нужен в sys.path, чтобы импортировать pipeline_trace / manual_control,
# лежащие рядом с start.py (а не внутри пакета debug_menu).
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PIPELINE_TRACE_PATH = PROJECT_ROOT / "logs" / "pipeline_trace.jsonl"
MEMORY_PATH = PROJECT_ROOT / "memory.json"
MANUAL_QUEUE_PATH = PROJECT_ROOT / "cache" / "manual_commands.json"
BANWORDS_PATH = PROJECT_ROOT / "data" / "speech_banwords.txt"
PROMPT_SNAPSHOT_PATH = PROJECT_ROOT / "cache" / "last_prompt.txt"

# Человекочитаемые названия этапов трассы.
_STAGE_LABELS = {
    "in": "получен ввод",
    "prompt": "собран промпт",
    "llm_start": "LLM: старт",
    "llm_done": "LLM: ответ",
    "postprocess": "постобработка",
    "tts_start": "TTS: старт",
    "tts_done": "TTS: конец",
}


class _ToolScreen(tk.Frame):
    """Базовый экран инструмента: тёмный фон, заголовок, кнопка НАЗАД."""

    title = "ИНСТРУМЕНТ"

    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app
        self._poll_timer = None
        self._make_header(self.title)
        self._build()

    # --- шапка/подвал ---

    def _make_header(self, title):
        tk.Label(self, text="╔══════════════════════════════════════╗", bg=BG, fg=FG, font=FONT).pack()
        tk.Label(self, text=f"║  {title:<36}║", bg=BG, fg=AMBER, font=FONT).pack()
        tk.Label(self, text="╚══════════════════════════════════════╝", bg=BG, fg=FG, font=FONT).pack(pady=(0, 6))

    def _make_back_button(self):
        tk.Button(self, text="[ ← НАЗАД ]", bg=BG, fg=FG, font=FONT, activebackground=DIM,
                  activeforeground=AMBER, relief="flat", cursor="hand2",
                  command=self._close).pack(pady=(6, 0))

    def _make_text_area(self, height=22):
        frame = tk.Frame(self, bg=BG)
        frame.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(frame, orient="vertical")
        text = tk.Text(frame, bg="#101010", fg=FG, font=FONT_SM, wrap="word",
                       state="disabled", yscrollcommand=vsb.set, height=height)
        vsb.config(command=text.yview)
        text.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text.tag_config("err", foreground=RED_ERR)
        text.tag_config("warn", foreground=AMBER)
        text.tag_config("hi", foreground="#66ccff")
        text.tag_config("dim", foreground=DIM)
        return text

    @staticmethod
    def _set_text(widget, content, tag=None):
        widget.config(state="normal")
        widget.delete("1.0", tk.END)
        if tag:
            widget.insert(tk.END, content, tag)
        else:
            widget.insert(tk.END, content)
        widget.config(state="disabled")

    def _build(self):  # переопределяется наследниками
        raise NotImplementedError

    def _close(self):
        try:
            if self._poll_timer is not None:
                self.after_cancel(self._poll_timer)
        except Exception:
            pass
        self.app.show_main_menu()


# --------------------------------------------------------------------------- #
# ТРАССА ЗАПРОСА / ТРАССА ОЗВУЧКИ
# --------------------------------------------------------------------------- #
class _TraceScreen(_ToolScreen):
    """Общий экран трассы: показывает цепочки этапов из pipeline_trace.jsonl.

    Наследники фильтруют этапы (весь пайплайн vs только озвучка).
    """

    stage_filter = None  # None = все этапы; иначе множество имён стадий

    def _build(self):
        self.text = self._make_text_area()
        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=(4, 0))
        tk.Button(btns, text="[ ↻ ОБНОВИТЬ ]", bg=BG, fg=FG, font=FONT_SM, relief="flat",
                  activebackground=DIM, activeforeground=AMBER, cursor="hand2",
                  command=self._refresh).pack(side="left", padx=4)
        self._make_back_button()
        self._refresh()
        self._poll_timer = self.after(2000, self._auto_poll)

    def _auto_poll(self):
        if self.winfo_exists():
            self._refresh()
            self._poll_timer = self.after(2000, self._auto_poll)

    def _refresh(self):
        try:
            import core.pipeline_trace as pipeline_trace
        except Exception:
            self._set_text(self.text, "Модуль pipeline_trace недоступен.\n", "warn")
            return
        try:
            entries = pipeline_trace.read_recent(400)
            chains = pipeline_trace.group_by_request(entries)
        except Exception as exc:  # noqa: BLE001
            self._set_text(self.text, f"Ошибка чтения трассы: {exc}\n", "err")
            return
        if not chains:
            self._set_text(self.text, "Трасса пуста — запусти проект и поговори с Нимфеей.\n", "dim")
            return

        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        for chain in chains[-30:]:
            stages = chain.get("stages", [])
            if self.stage_filter is not None:
                stages = [s for s in stages if str(s.get("stage")) in self.stage_filter]
            if not stages:
                continue
            rid = chain.get("request_id", "") or "—"
            total = chain.get("total_ms", 0)
            self.text.insert(tk.END, f"► запрос {rid}   всего {total} мс\n", "hi")
            for stage in stages:
                name = str(stage.get("stage", "?"))
                label = _STAGE_LABELS.get(name, name)
                delta = stage.get("delta_ms", 0)
                extra = stage.get("text") or stage.get("info") or ""
                extra = (" — " + str(extra)[:60]) if extra else ""
                self.text.insert(tk.END, f"    {label:<16} +{delta:>5} мс{extra}\n")
            self.text.insert(tk.END, "\n")
        self.text.see(tk.END)
        self.text.config(state="disabled")


class RequestTraceScreen(_TraceScreen):
    title = "ТРАССА ЗАПРОСА"
    stage_filter = None  # весь пайплайн


class TtsTraceScreen(_TraceScreen):
    title = "ТРАССА ОЗВУЧКИ"
    stage_filter = {"tts_start", "tts_done"}


# --------------------------------------------------------------------------- #
# ПРОМПТ — снимок последнего собранного промпта
# --------------------------------------------------------------------------- #
class PromptScreen(_ToolScreen):
    title = "ПРОМПТ"

    def _build(self):
        tk.Label(self, text="Последний промпт, собранный для реплики (cache/last_prompt.txt).",
                 bg=BG, fg=DIM, font=FONT_SM).pack(pady=(0, 4))
        self.text = self._make_text_area()
        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=(4, 0))
        tk.Button(btns, text="[ ↻ ОБНОВИТЬ ]", bg=BG, fg=FG, font=FONT_SM, relief="flat",
                  activebackground=DIM, activeforeground=AMBER, cursor="hand2",
                  command=self._refresh).pack(side="left", padx=4)
        self._make_back_button()
        self._refresh()

    def _refresh(self):
        try:
            content = PROMPT_SNAPSHOT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ("Снимок промпта ещё не создан.\n\n"
                       "Он появится после первого ответа Нимфеи: build_pipeline_prompt "
                       "сохраняет последний собранный промпт в cache/last_prompt.txt.\n")
        except Exception as exc:  # noqa: BLE001
            content = f"Ошибка чтения промпта: {exc}\n"
        self._set_text(self.text, content)


# --------------------------------------------------------------------------- #
# РУЧНОЙ ВВОД — файловая очередь в main loop
# --------------------------------------------------------------------------- #
class ManualInputScreen(_ToolScreen):
    title = "РУЧНОЙ ВВОД"

    def _build(self):
        hint = (
            "Отправляет реплику в очередь cache/manual_commands.json (её читает main loop).\n"
            "  •  «реплика»        — от лица пользователя;\n"
            "  •  «Нима: реплика»  — она проговаривает сразу, минуя LLM;\n"
            "  •  «Имя: реплика»   — запрос от любого лица."
        )
        tk.Label(self, text=hint, bg=BG, fg=DIM, font=FONT_SM, justify="left").pack(anchor="w", pady=(0, 6))

        self.entry = tk.Entry(self, bg="#101010", fg=FG, font=FONT, insertbackground=FG,
                              relief="flat", width=52)
        self.entry.pack(fill="x", pady=(0, 6))
        self.entry.bind("<Return>", lambda _e: self._send())

        btns = tk.Frame(self, bg=BG)
        btns.pack()
        tk.Button(btns, text="[ ОТПРАВИТЬ ]", bg=BG, fg=FG, font=FONT, relief="flat",
                  activebackground=DIM, activeforeground=AMBER, cursor="hand2",
                  command=self._send).pack(side="left", padx=4)
        tk.Button(btns, text="[ ↻ ПЕРЕГЕНЕРИРОВАТЬ ]", bg=BG, fg=AMBER, font=FONT, relief="flat",
                  activebackground=DIM, activeforeground=FG, cursor="hand2",
                  command=self._regenerate).pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM).pack(pady=(4, 0))

        self.text = self._make_text_area(height=10)
        self._make_back_button()
        self._refresh_queue()

    def _manual(self):
        from pipeline import manual_control
        return manual_control

    def _send(self):
        raw = self.entry.get().strip()
        if not raw:
            return
        try:
            mc = self._manual()
            mc.enqueue(raw)
            mc.remember_last(raw)
            self.status_var.set(f"Отправлено: {raw[:48]}")
            self.entry.delete(0, tk.END)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Ошибка: {exc}")
        self._refresh_queue()

    def _regenerate(self):
        """Повторяет последний ручной ввод. Спец-маркер REGEN просит main loop
        обойти кэш ответов (см. обработку manual-очереди в start.py)."""
        try:
            mc = self._manual()
            last = mc.regenerate_last()
            if not last:
                self.status_var.set("Нет предыдущего ввода для повтора.")
                return
            mc.enqueue("!regen " + last)
            self.status_var.set(f"Повтор (мимо кэша): {last[:40]}")
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Ошибка: {exc}")
        self._refresh_queue()

    def _refresh_queue(self):
        try:
            items = json.loads(MANUAL_QUEUE_PATH.read_text(encoding="utf-8"))
        except Exception:
            items = []
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        if not items:
            self.text.insert(tk.END, "Очередь пуста (main loop разбирает её на лету).\n", "dim")
        else:
            self.text.insert(tk.END, "В очереди на разбор:\n", "hi")
            for entry in items[-20:]:
                if isinstance(entry, dict):
                    self.text.insert(tk.END, f"  • {str(entry.get('text', ''))[:60]}\n")
        self.text.config(state="disabled")


# --------------------------------------------------------------------------- #
# БАНВОРДЫ — редактор списка масок для озвучки
# --------------------------------------------------------------------------- #
class BanwordsScreen(_ToolScreen):
    title = "БАНВОРДЫ"

    def _build(self):
        tk.Label(self, text="Слова из этого списка маскируются звёздочками перед TTS и субтитрами.",
                 bg=BG, fg=DIM, font=FONT_SM).pack(anchor="w")
        tk.Label(self, text="По одному слову в строке. Строка, начинающаяся с #, — комментарий.",
                 bg=BG, fg=DIM, font=FONT_SM).pack(anchor="w", pady=(0, 6))

        frame = tk.Frame(self, bg=BG)
        frame.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(frame, orient="vertical")
        self.editor = tk.Text(frame, bg="#101010", fg=FG, font=FONT_SM, wrap="none",
                              insertbackground=FG, yscrollcommand=vsb.set, height=18)
        vsb.config(command=self.editor.yview)
        self.editor.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=(4, 0))
        tk.Button(btns, text="[ 💾 СОХРАНИТЬ ]", bg=BG, fg=FG, font=FONT, relief="flat",
                  activebackground=DIM, activeforeground=AMBER, cursor="hand2",
                  command=self._save).pack(side="left", padx=4)
        self.status_var = tk.StringVar(value="")
        tk.Label(btns, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM).pack(side="left", padx=8)

        self._make_back_button()
        self._load()

    def _load(self):
        try:
            content = BANWORDS_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = "# Список банвордов для озвучки Нимфеи (по слову в строке)\n"
        except Exception as exc:  # noqa: BLE001
            content = f"# Ошибка чтения: {exc}\n"
        self.editor.delete("1.0", tk.END)
        self.editor.insert(tk.END, content)

    def _save(self):
        try:
            BANWORDS_PATH.parent.mkdir(parents=True, exist_ok=True)
            BANWORDS_PATH.write_text(self.editor.get("1.0", "end-1c"), encoding="utf-8")
            self.status_var.set("Сохранено ✓")
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Ошибка: {exc}")


# --------------------------------------------------------------------------- #
# ИСТОРИЯ ДИАЛОГОВ — из memory.json
# --------------------------------------------------------------------------- #
class DialogHistoryScreen(_ToolScreen):
    title = "ИСТОРИЯ ДИАЛОГОВ"

    def _build(self):
        self.text = self._make_text_area()
        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=(4, 0))
        tk.Button(btns, text="[ ↻ ОБНОВИТЬ ]", bg=BG, fg=FG, font=FONT_SM, relief="flat",
                  activebackground=DIM, activeforeground=AMBER, cursor="hand2",
                  command=self._refresh).pack(side="left", padx=4)
        self._make_back_button()
        self._refresh()

    @staticmethod
    def _extract_dialog(data):
        """Достаёт список реплик из memory.json (схема v2 со слоями)."""
        if not isinstance(data, dict):
            return []
        # Схема v2: {"layers": {"dialog": [...]}} или прямой ключ "dialog".
        layers = data.get("layers") if isinstance(data.get("layers"), dict) else data
        dialog = layers.get("dialog") if isinstance(layers, dict) else None
        if isinstance(dialog, dict):
            dialog = dialog.get("items") or dialog.get("entries") or []
        return dialog if isinstance(dialog, list) else []

    @staticmethod
    def _fmt_ts(entry):
        ts = entry.get("ts") or entry.get("time") or entry.get("timestamp")
        try:
            return time.strftime("%d.%m %H:%M", time.localtime(float(ts)))
        except Exception:
            return ""

    def _refresh(self):
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._set_text(self.text, "memory.json не найден.\n", "warn")
            return
        except Exception as exc:  # noqa: BLE001
            self._set_text(self.text, f"Ошибка чтения памяти: {exc}\n", "err")
            return

        dialog = self._extract_dialog(data)
        if not dialog:
            self._set_text(self.text, "История диалога пуста.\n", "dim")
            return

        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        for entry in dialog[-80:]:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role") or entry.get("speaker") or "").lower()
            content = str(entry.get("text") or entry.get("content") or entry.get("message") or "")
            when = self._fmt_ts(entry)
            prefix = f"[{when}] " if when else ""
            if role in ("assistant", "vita", "nima", "нимфея", "bot"):
                self.text.insert(tk.END, f"{prefix}Нимфея: {content}\n", "hi")
            else:
                who = entry.get("speaker") or "ты"
                self.text.insert(tk.END, f"{prefix}{who}: {content}\n")
        self.text.see(tk.END)
        self.text.config(state="disabled")


class DonationTestScreen(_ToolScreen):
    """Тестовый донат в работающую Нимфею (twitch.twitch_donations)."""

    title = "ТЕСТ-ДОНАТ"

    def _build(self):
        tk.Label(self, text="Вставляет донат в приёмник twitch.twitch_donations.\n"
                            "Работает, только пока запущен start.py: Нимфея поблагодарит\n"
                            "на ближайшей паузе (во время речи/генерации донат ждёт).",
                 bg=BG, fg=DIM, font=FONT_SM, justify="left").pack(anchor="w", pady=(0, 8))

        self.donor_var = tk.StringVar(value="TestViewer")
        self.amount_var = tk.StringVar(value="250")
        self.currency_var = tk.StringVar(value="RUB")
        self.message_var = tk.StringVar(value="Держи на печеньку!")
        for label, var in (("Имя донатера:", self.donor_var), ("Сумма:", self.amount_var),
                           ("Валюта:", self.currency_var), ("Сообщение:", self.message_var)):
            row = tk.Frame(self, bg=BG)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, bg=BG, fg=DIM, font=FONT, width=16, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=var, bg="#101010", fg=FG, font=FONT,
                     insertbackground=FG, relief="flat", width=40).pack(side="left", fill="x", expand=True)

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=(10, 0))
        tk.Button(btns, text="[ ВСТАВИТЬ ДОНАТ ]", bg=BG, fg=AMBER, font=FONT, relief="flat",
                  activebackground=DIM, activeforeground=FG, cursor="hand2",
                  command=self._inject).pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM).pack(pady=(6, 0))

        self.text = self._make_text_area(height=8)
        self._make_back_button()
        self._show_hint()

    def _inject(self):
        """Кросс-процессная инъекция: меню живёт в своём pythonw-процессе,
        поэтому пробуем HTTP приёмник start.py, при недоступности — файловый
        ящик data/donations_inbox.jsonl (start.py разбирает его раз в 2 с)."""
        payload = {
            "donor": self.donor_var.get().strip() or "Зритель",
            "amount": self.amount_var.get().strip() or "100",
            "currency": self.currency_var.get().strip() or "RUB",
            "message": self.message_var.get().strip(),
        }
        try:
            import urllib.request
            req = urllib.request.Request(
                "http://127.0.0.1:8791/donation",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2):
                self.status_var.set(f"Донат принят по HTTP: {payload['donor']} / {payload['amount']}")
                return
        except Exception:
            pass
        try:
            inbox = PROJECT_ROOT / "data" / "donations_inbox.jsonl"
            inbox.parent.mkdir(parents=True, exist_ok=True)
            with inbox.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self.status_var.set("HTTP приёмник недоступен — донат положен в файловый ящик.")
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Ошибка: {exc}")

    def _show_hint(self):
        self._set_text(self.text,
                       "HTTP-приём (для вебхуков StreamElements/мостов):\n"
                       "POST http://127.0.0.1:8791/donation\n"
                       '{"donor":"Имя","amount":100,"currency":"RUB","message":"текст"}\n\n'
                       "Файловый ящик: data/donations_inbox.jsonl — по одному JSON-донату в строке.",
                       "dim")


# --------------------------------------------------------------------------- #
# ЗАПИСЬ ГОЛОСА — голосовой профиль для voice_id (enroll)
# --------------------------------------------------------------------------- #
class VoiceEnrollScreen(_ToolScreen):
    title = "ЗАПИСЬ ГОЛОСА"

    RECORD_SEC = 6.0

    def _build(self):
        hint = ("Запиши голос говорящего (6 секунд, обычным тоном) — профиль уйдёт\n"
                "в память, и voice_id будет узнавать этого человека по голосу.\n"
                "Нимфея должна быть ЗАПУЩЕНА: запись она забирает через main loop.")
        tk.Label(self, text=hint, bg=BG, fg=DIM, font=FONT_SM, justify="left").pack(anchor="w", pady=(0, 6))

        row = tk.Frame(self, bg=BG)
        row.pack(anchor="w")
        tk.Label(row, text="Имя говорящего:", bg=BG, fg=FG, font=FONT_SM).pack(side="left")
        self.name_entry = tk.Entry(row, bg="#101010", fg=FG, font=FONT, insertbackground=FG,
                                   relief="flat", width=22)
        self.name_entry.insert(0, "Кизилл")
        self.name_entry.pack(side="left", padx=(6, 0))

        # Источник: свой голос — с микрофона, чужой (друг из созвона/стрима) —
        # со звука системы (тот же WASAPI loopback, что слушают уши Нимфы).
        self.source_var = tk.StringVar(value="mic")
        src = tk.Frame(self, bg=BG)
        src.pack(anchor="w", pady=(6, 0))
        tk.Label(src, text="Источник:", bg=BG, fg=FG, font=FONT_SM).pack(side="left")
        tk.Radiobutton(src, text="Микрофон", variable=self.source_var, value="mic",
                       bg=BG, fg=FG, selectcolor="#101010", activebackground=BG,
                       font=FONT_SM, command=self._source_changed).pack(side="left", padx=(8, 4))
        tk.Radiobutton(src, text="Звук системы (созвон/игра)", variable=self.source_var,
                       value="loop", bg=BG, fg=FG, selectcolor="#101010",
                       activebackground=BG, font=FONT_SM,
                       command=self._source_changed).pack(side="left", padx=4)

    def _source_changed(self):
        loop = self.source_var.get() == "loop"
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, "Толик" if loop else "Кизилл")

        btns = tk.Frame(self, bg=BG)
        btns.pack(pady=6)
        self.btn_record = tk.Button(btns, text="[ ● ЗАПИСАТЬ 6 СЕК ]", bg=BG, fg=FG, font=FONT,
                                    relief="flat", activebackground=DIM, activeforeground=AMBER,
                                    cursor="hand2", command=self._record)
        self.btn_record.pack(side="left", padx=4)

        self.status_var = tk.StringVar(value="Готов к записи.")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM,
                 wraplength=560, justify="left").pack(pady=(2, 4))

        delrow = tk.Frame(self, bg=BG)
        delrow.pack(anchor="w")
        tk.Label(delrow, text="Удалить профиль:", bg=BG, fg=DIM,
                 font=FONT_SM).pack(side="left")
        self.del_entry = tk.Entry(delrow, bg="#101010", fg=FG, font=FONT,
                                  insertbackground=FG, relief="flat", width=16)
        self.del_entry.pack(side="left", padx=(6, 4))
        self.btn_del = tk.Button(delrow, text="[ ✖ УДАЛИТЬ ]", bg=BG, fg="#e07070",
                                 font=FONT_SM, relief="flat",
                                 activebackground=DIM, activeforeground="#ff5050",
                                 cursor="hand2", command=self._delete_profile)
        self.btn_del.pack(side="left")

        self.text = self._make_text_area(height=9)
        self._make_back_button()
        self._refresh_profiles()

    def _delete_profile(self):
        name = self.del_entry.get().strip()
        if not name:
            self.status_var.set("Впиши имя профиля для удаления.")
            return
        from pipeline import manual_control
        self._enroll_started = time.time()
        manual_control.enqueue_cmd({"cmd": "delete_voice_profile", "name": name})
        self.status_var.set(f"Команда удаления «{name}» ушла в Нимфею…")
        self.after(4000, lambda: self._check_result(expect="удалён"))

    # --- профили в памяти ---
    def _refresh_profiles(self):
        try:
            data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            people = data.get("layers", {}).get("people", {}) or {}
            lines = ["Известные голосовые профили:"]
            for key, person in people.items():
                embs = person.get("voice_embeddings") if isinstance(person, dict) else None
                if isinstance(embs, list) and embs:
                    lines.append(f"  • {person.get('name') or key} — эталонов: {len(embs)}")
            self._set_text(self.text, "\n".join(lines))
        except Exception as exc:  # noqa: BLE001
            self._set_text(self.text, f"Не прочитать memory.json: {exc}", "err")

    # --- запись ---
    def _record(self):
        name = self.name_entry.get().strip()
        if not name:
            self.status_var.set("Впиши имя говорящего.")
            return
        self.btn_record.config(state="disabled")
        loop = self.source_var.get() == "loop"
        if loop:
            self.status_var.set(f"Запись… пусть друг говорит в созвон "
                                f"{self.RECORD_SEC:.0f} секунд обычным тоном.")
        else:
            self.status_var.set(f"Запись… говори в микрофон "
                                f"{self.RECORD_SEC:.0f} секунд обычным тоном.")
        threading.Thread(target=self._record_thread, args=(name, loop),
                         daemon=True).start()

    def _record_thread(self, name: str, loop: bool = False):
        import wave

        import numpy as np
        try:
            duration = self.RECORD_SEC
            if loop:
                audio, dev_name = self._record_loopback(duration)
            else:
                audio, dev_name = self._record_mic(duration)
            rms = float(np.sqrt(np.mean(audio * audio)))
            wav_path = PROJECT_ROOT / "cache" / f"enroll_{name.strip()}.wav"
            wav_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes((np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes())
            if rms < 0.002:
                self._set_status(f"Запись почти тихая (RMS {rms:.4f}, источник: "
                                 f"{dev_name or 'по умолчанию'}) — проверь источник "
                                 "и попробуй ещё раз.")
                self._done()
                return
            from pipeline import manual_control
            self._enroll_started = time.time()
            manual_control.enqueue_cmd({"cmd": "enroll_voice", "name": name,
                                        "wav": str(wav_path)})
            self._set_status(f"Записано {duration:.0f} с ({dev_name or 'по умолчанию'}, "
                             f"RMS {rms:.3f}) → команда ушла в Нимфею. "
                             "Результат появится ниже через несколько секунд.")
            self.after(4000, self._check_result)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"Ошибка записи: {exc}")
            self._done()

    def _record_mic(self, duration: float):
        """Свой голос: физический микрофон (env NIMA_STT_MIC_DEVICE может быть
        не задан в процессе меню — тогда ищем сами, исключая виртуальные входы
        VoiceMeeter/CABLE: там почти тишина)."""
        import sounddevice as sd
        device, dev_name = self._resolve_input_device(sd)
        gain = 3.0
        try:
            from core.config import STT_MIC_GAIN
            gain = STT_MIC_GAIN
        except Exception:  # noqa: BLE001
            pass
        audio = sd.rec(int(duration * 16000), samplerate=16000, channels=1,
                       dtype="int16", device=device)
        sd.wait()
        audio = audio.astype(np.float32) / 32768.0
        if gain != 1.0:
            audio = audio * gain
        # автонормализация: ECAPA всё равно работает на нормализованном
        # звуке, а тихий DEXP (RMS ~0.002) без этого — глухая запись
        rms = float(np.sqrt(np.mean(audio * audio)))
        if rms > 0.0005:
            target = 0.035
            audio = np.clip(audio * (target / rms), -1.0, 1.0)
        return audio.flatten().astype(np.float32), dev_name

    def _record_loopback(self, duration: float):
        """Чужой голос: WASAPI loopback дефолтного вывода (весь звук системы —
        тот же источник, что слушает loopback-канал ушей Нимфы)."""
        import numpy as np
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        try:
            try:
                dev = pa.get_default_wasapi_loopback()
            except OSError as exc:
                raise RuntimeError(f"loopback не открылся: {exc}")
            rate = int(dev["defaultSampleRate"])
            block = int(rate * 0.1)
            stream = pa.open(format=pyaudio.paInt16, channels=2, rate=rate,
                             input=True, input_device_index=int(dev["index"]),
                             frames_per_buffer=block)
            stream.start_stream()
            chunks = []
            got = 0.0
            while got < duration:
                chunks.append(np.frombuffer(
                    stream.read(block, exception_on_overflow=False), dtype=np.int16))
                got += block / rate
            stream.stop_stream()
            stream.close()
            audio = np.concatenate(chunks).reshape(-1, 2).mean(axis=1)  # stereo→mono
            # ресемпл в 16 кГц (частота whisper/ECAPA) линейной интерполяцией
            n_out = int(len(audio) * 16000 / rate)
            audio = np.interp(np.linspace(0, len(audio) - 1, n_out),
                              np.arange(len(audio)), audio)
            audio = (audio / 32768.0).astype(np.float32)
            # loopback системный, усиление STT не нужно — только нормализация
            rms = float(np.sqrt(np.mean(audio * audio)))
            if rms > 0.0005:
                audio = np.clip(audio * (0.035 / rms), -1.0, 1.0)
            return audio, f"звук системы: {dev['name']}"
        finally:
            pa.terminate()

    @staticmethod
    def _resolve_input_device(sd):
        """(индекс, имя) микрофона для записи. Приоритет: NIMA_STT_MIC_DEVICE
        (если задан в этом процессе) → первый ФИЗИЧЕСКИЙ вход (исключаем
        VoiceMeeter/CABLE/виртуальные — системный дефолт тут виртуальный и
        тихий). Возвращает (None, "") только если входов нет вообще."""
        preferred = ""
        try:
            from core.config import STT_MIC_DEVICE
            preferred = (STT_MIC_DEVICE or "").strip().lower()
        except Exception:  # noqa: BLE001
            pass
        _VIRTUAL = ("voicemeeter", "cable", "virtual", "vb-audio", "voice mod",
                    "voicemod", "sound mapper")
        physical = None
        try:
            for idx, dev in enumerate(sd.query_devices()):
                if dev.get("max_input_channels", 0) <= 0:
                    continue
                dname = str(dev.get("name", "")).strip()
                low = dname.lower()
                if preferred and preferred in low:
                    return idx, dname
                if physical is None and not any(v in low for v in _VIRTUAL):
                    physical = (idx, dname)
        except Exception:  # noqa: BLE001
            pass
        return physical if physical else (None, "")

    def _set_status(self, text: str):
        try:
            self.status_var.set(text)
        except Exception:  # noqa: BLE001 — окно уже закрыто
            pass

    def _done(self):
        try:
            self.btn_record.config(state="normal")
        except Exception:  # noqa: BLE001
            pass

    def _check_result(self, attempt: int = 0, expect: str = "записан"):
        # Результат валиден, только если он НОВЕЕ отправки команды: файл может
        # хранить ответ прошлой записи, а Нимфа может быть вообще выключена.
        started = getattr(self, "_enroll_started", 0.0)
        try:
            from core.config import ENROLL_RESULT_PATH
            payload = json.loads(ENROLL_RESULT_PATH.read_text(encoding="utf-8"))
            ts = time.mktime(time.strptime(payload.get("ts", ""), "%Y-%m-%d %H:%M:%S"))
            if ts + 2 < started:
                raise ValueError("результат от прошлой записи")
            ok = bool(payload.get("ok"))
            self._set_status(("ГОТОВО: " if ok else "ОШИБКА: ")
                             + str(payload.get("detail", "")))
        except Exception:  # noqa: BLE001 — старый/кривой ответ: подождём ещё
            if attempt < 5:
                self.after(2000, lambda: self._check_result(attempt + 1, expect))
                return
            self._set_status(f"Нимфа не ответила на команду ({expect}). Она запущена? "
                             "Запусти её (ЗАПУСК/РЕСТАРТ в меню) и попробуй снова.")
        self._done()
        self._refresh_profiles()
