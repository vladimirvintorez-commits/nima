import re
import shutil
import threading
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

from .archive import find_backups, make_archive, make_incremental_archive
from .config import AMBER, ANIMATIONS, BG, CHANGELOG_PATH, DIM, EMOTIONS, FG, FONT, FONT_LG, FONT_SM, PROJECT_ROOT, RED_ERR, TRANSPARENT, VITA_MODEL_DIR, VITA_VRM_PATH, get_version, version_from_name
from .module_tests import test_all_modules
from .restore import read_incremental_manifest, restore_archive, restore_incremental_archive


class BaseScreen(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, bg=BG)
        self.app = app

    def destroy_self(self):
        self.destroy()

    def _make_header(self, title):
        tk.Label(self, text="╔══════════════════════════════════════╗", bg=BG, fg=FG, font=FONT).pack()
        tk.Label(self, text=f"║  {title:<36}║", bg=BG, fg=AMBER, font=FONT).pack()
        tk.Label(self, text="╚══════════════════════════════════════╝", bg=BG, fg=FG, font=FONT).pack(pady=(0, 8))

    def _make_back_button(self, row_widget=None):
        btn = tk.Button(
            self, text="[ ← НАЗАД ]", bg=BG, fg=FG, font=FONT,
            activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
            command=self.app.show_main_menu,
        )
        btn.pack(pady=(8, 4))
        if hasattr(self, "_register_focusable"):
            self._register_focusable(btn, self.app.show_main_menu)
        return btn

    def _init_keyboard_nav(self):
        self._focusables = []
        self._focus_index = 0
        self.bind_all("<Escape>", self._go_back_key)
        for key in ("<Up>", "<Down>", "<Left>", "<Right>", "<Tab>"):
            self.bind_all(key, self._arrow_key)
        self.bind_all("<Return>", self._activate_key)
        self.bind_all("<KP_Enter>", self._activate_key)
        self.bind_all("<space>", self._activate_key)

    def _register_focusable(self, widget, action=None, arrow_nav=True):
        self._focusables.append((widget, action))
        widget.bind("<FocusIn>", lambda e, w=widget: self._style_focus(w, True))
        widget.bind("<FocusOut>", lambda e, w=widget: self._style_focus(w, False))
        widget.bind("<Return>", lambda e, a=action: self._activate_focusable(a))
        widget.bind("<KP_Enter>", lambda e, a=action: self._activate_focusable(a))
        if arrow_nav:
            widget.bind("<Up>", self._focus_prev)
            widget.bind("<Left>", self._focus_prev)
            widget.bind("<Down>", self._focus_next)
            widget.bind("<Right>", self._focus_next)
        return widget

    def _style_focus(self, widget, focused):
        try:
            if isinstance(widget, tk.Button):
                widget.config(fg=AMBER if focused else FG, bg=DIM if focused else BG)
        except tk.TclError:
            pass

    def _focus_widget(self, index=0):
        if not self._focusables:
            return
        self._focus_index = index % len(self._focusables)
        widget, _ = self._focusables[self._focus_index]
        try:
            widget.focus_set()
        except tk.TclError:
            pass

    def _focus_prev(self, event=None):
        self._focus_widget(self._focus_index - 1)
        return "break"

    def _focus_next(self, event=None):
        self._focus_widget(self._focus_index + 1)
        return "break"

    def _is_text_input_focus(self):
        widget = self.focus_get()
        return isinstance(widget, (tk.Entry, tk.Text))

    def _arrow_key(self, event=None):
        if self._is_text_input_focus():
            return None
        if event and event.keysym in ("Up", "Left"):
            return self._focus_prev(event)
        return self._focus_next(event)
    def _activate_key(self, event=None):
        if self._is_text_input_focus():
            return None
        return self._activate_focusable()

    def _activate_focusable(self, action=None):
        if action:
            action()
            return "break"
        widget = self.focus_get()
        if isinstance(widget, tk.Button):
            widget.invoke()
            return "break"
        return None

    def _go_back_key(self, event=None):
        if self._is_text_input_focus():
            return None
        self.app.show_main_menu()
        return "break"


def strip_window_frame(root):
    """Срезаем системные стили окна (заголовок/рамку) через WinAPI. После
    withdraw/deiconify Windows восстанавливает стили — вызывать при каждом
    показе Tk-экрана."""
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        GWL_STYLE = -16
        WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
        WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
        style &= ~(WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style)
        SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                          SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
        # DWM дорисовывает контур/тень поверх стилей — отключаем его рендеринг
        # и явно гасим цвет границы (Windows 11).
        try:
            dwm = ctypes.windll.dwmapi
            DWMWA_NCRENDERING_POLICY, DWMNCRP_DISABLED = 2, 1
            dwm.DwmSetWindowAttribute(hwnd, DWMWA_NCRENDERING_POLICY,
                                      ctypes.byref(ctypes.c_int(DWMNCRP_DISABLED)), 4)
            DWMWA_BORDER_COLOR, DWMWA_COLOR_NONE = 34, 0xFFFFFFFE
            dwm.DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR,
                                      ctypes.byref(ctypes.c_int(DWMWA_COLOR_NONE)), 4)
        except Exception:
            pass
    except Exception:
        pass


def set_window_shape(root, shape):
    """Режем окно регионом: 'circle' — окно буквально становится кругом
    (квадратные углы отсекаются системой: ни рамки, ни края, ни тени),
    иначе — обычный прямоугольник."""
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        if shape == "circle":
            w = root.winfo_width() or 760
            h = root.winfo_height() or 760
            rgn = ctypes.windll.gdi32.CreateEllipticRgn(0, 0, w + 1, h + 1)
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        else:
            ctypes.windll.user32.SetWindowRgn(hwnd, 0, True)
    except Exception:
        pass


class MainMenuApp:
    # Иконки пунктов для радиального вида (ключ = action).
    ICONS = {
        "launch_bot": "▶", "stop_bot": "■", "restart_bot": "↻", "console": "▤", "save": "⛁", "restore": "⇩",
        "full_test": "☑", "bug_reports": "⚠", "smoke_test": "⚗", "task_board": "☰",
        "logs": "▦", "playground": "♞", "quiz": "★", "twitch_quiz": "♦",
        "generated_phrases_review": "✎", "notes": "❖", "exit": "✕", "test": "⚑",
        "trace_request": "⇄", "trace_tts": "♪", "prompt_view": "❝", "manual_input": "⌨",
        "banwords": "⊘", "dialog_history": "❐", "phone": "📱", "errors": "⚠",
        "voice_enroll": "🎙",
    }

    # Группы пунктов: заголовок секции + элементы. Навигация пропускает заголовки.
    GROUPS = [
        ("── ЗАПУСК ────────────────────────", [
            ("[ ▶ ЗАПУСТИТЬ НИМФЕЮ · start.py ]", "launch_bot"),
            ("[ ■ ВЫКЛЮЧИТЬ ПРОЕКТ ]", "stop_bot"),
            ("[ ↻ РЕСТАРТ · стоп→старт ]", "restart_bot"),
            ("[ КОНСОЛЬ · лог·статус ]", "console"),
        ]),
        ("── БЭКАП ──────────────────────────", [
            ("[ SAVE ]", "save"),
            ("[ RESTORE ]", "restore"),
        ]),
        ("── ДИАГНОСТИКА ────────────────────", [
            ("[ ПОЛНЫЙ ТЕСТ СИСТЕМЫ ]", "full_test"),
            ("[ БАГИ — заметить баг ]", "bug_reports"),
            ("[ СМОК-ТЕСТ — проверить механики ]", "smoke_test"),
            ("[ ЗАДАЧИ — чек-лист ]", "task_board"),
            ("[ ОШИБКИ — коды и журнал ]", "errors"),
            ("[ VIEW LOGS ]", "logs"),
        ]),
        ("── ПЕРСОНАЖ ───────────────────────", [
            ("[ ПЕСОЧНИЦА · анимация·одежда·эмоция ]", "playground"),
        ]),
        ("── ОЦЕНКА ОТВЕТОВ ─────────────────", [
            ("[ Оценка ответов Виты ]", "quiz"),
            ("[ Оценка Twitch-ответов ]", "twitch_quiz"),
            ("[ Оценка сгенерированных реплик ]", "generated_phrases_review"),
        ]),
        ("── ИНСТРУМЕНТЫ ────────────────────", [
            ("[ ТЕЛЕФОН · Нимфея в кармане ]", "phone"),
            ("[ ТРАССА ЗАПРОСА · этапы пайплайна ]", "trace_request"),
            ("[ ТРАССА ОЗВУЧКИ · TTS ]", "trace_tts"),
            ("[ ПРОМПТ · последний собранный ]", "prompt_view"),
            ("[ РУЧНОЙ ВВОД · очередь в main loop ]", "manual_input"),
            ("[ ЗАПИСЬ ГОЛОСА · профиль voice_id ]", "voice_enroll"),
            ("[ ТЕСТ-ДОНАТ · вставить донат ]", "donation_test"),
            ("[ БАНВОРДЫ · маски для озвучки ]", "banwords"),
            ("[ ИСТОРИЯ ДИАЛОГОВ · memory.json ]", "dialog_history"),
        ]),
        ("───────────────────────────────────", [
            ("[ PATCH NOTES ]", "notes"),
            ("[ EXIT ]", "exit"),
        ]),
    ]

    def __init__(self, root):
        self.root = root
        self.ring = None         # RingBridge (Electron-кольцо), назначается в app.py
        self.root.title("Neyronya • Нимфея — Debug Menu")
        self.root.configure(bg=TRANSPARENT)
        self.root.resizable(False, False)
        # Прозрачность «как у окна аватара»: цвет TRANSPARENT нигде не рисуется
        # кроме пустого фона — сквозь него видно рабочий стол, клики проходят
        # мимо окна. Экраны-вкладки используют обычный BG и остаются плотными.
        try:
            self.root.attributes("-transparentcolor", TRANSPARENT)
        except Exception:
            pass
        self.current_screen = None
        self._sel = 0
        self._fan_pos = 0.0        # плавная позиция карусели (анимируется к _sel)
        self._fan_anim_id = None
        self._frame_photo = None   # PhotoImage фона-рамки (держим ссылку)
        self._radial_pos = []      # [(kind, idx, a0, a1, r_in, r_out)] — сегменты радиального вида
        self._toggle_bbox = None   # зона клика переключателя вида меню
        self._open_category = None # индекс раскрытой категории (кольцо 2)
        self._radial_center = None # (cx, cy) центра колец для hit-теста
        self.menu_style = self._load_menu_style()
        set_window_shape(self.root, "circle" if self.menu_style == "radial" else "rect")
        self._build_main()

    STYLE_PATH = PROJECT_ROOT / "cache" / "debug_menu_style.json"

    def _load_menu_style(self) -> str:
        # Круг — основной формат: сохранённый «fan» мигрирует на radial,
        # чтобы после обновления меню открылось без рамки.
        try:
            import json
            style = str(json.loads(self.STYLE_PATH.read_text(encoding="utf-8")).get("style", "radial"))
        except Exception:
            style = "radial"
        return "radial" if style not in ("radial", "fan") else style

    def _save_menu_style(self) -> None:
        try:
            import json
            self.STYLE_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.STYLE_PATH.write_text(json.dumps({"style": self.menu_style}, ensure_ascii=False),
                                       encoding="utf-8")
        except Exception:
            pass

    def _toggle_menu_style(self) -> None:
        self.menu_style = "radial" if self.menu_style == "fan" else "fan"
        self._fan_pos = float(self._sel)
        self._open_category = None
        self._save_menu_style()
        set_window_shape(self.root, "circle" if self.menu_style == "radial" else "rect")
        self._draw_menu()

    def _items(self):
        return [item for _, items in self.GROUPS for item in items]

    # ------------------------------------------------------------------ #
    # Радиальное меню — сегментированные кольца (донат).
    # ------------------------------------------------------------------ #
    def _radial_categories(self):
        """Категории для радиального вида: (name, [(label, action), ...],
        flat_base) — flat_base = стартовый плоский индекс группы в _items()."""
        cats = []
        flat = 0
        for header, items in self.GROUPS:
            name = header.strip("─ \t").strip()
            if not name:
                name = "ЕЩЁ"
            cats.append((name, items, flat))
            flat += len(items)
        return cats

    def _short_caption(self, label_text):
        cap = label_text.strip("[] ")
        # убрать ведущий глиф-иконку, если есть
        parts = cap.split("·")[0].strip()
        cap = parts if parts else cap
        # отрезать одиночный ведущий символ-глиф вида "▶ ЗАПУСТИТЬ"
        toks = cap.split()
        if toks and len(toks[0]) == 1 and not toks[0].isalnum():
            cap = " ".join(toks[1:]) or cap
        if len(cap) > 16:
            cap = cap[:15] + "…"
        return cap

    # ------------------------------------------------------------------ #
    # Главное меню — карусель-полукруг: выбранный пункт крупнее и ближе,
    # соседние — мельче и дальше по дуге; вокруг — фэнтези-рамка.
    # ------------------------------------------------------------------ #

    _FAN_VISIBLE = 6      # сколько пунктов видно с каждой стороны от выбранного
    _FAN_STEP = 34        # вертикальный шаг между соседними пунктами
    _FAN_SHRINK = 0.14    # доля уменьшения шрифта на шаг от центра

    def _build_main(self):
        if self.current_screen:
            self.current_screen.destroy()
            self.current_screen = None
        set_window_shape(self.root, "circle" if self.menu_style == "radial" else "rect")
        self._canvas = tk.Canvas(self.root, bg=TRANSPARENT, highlightthickness=0, cursor="arrow")
        self._canvas.pack(fill="both", expand=True)
        self.current_screen = self._canvas
        self._drag_start = None
        self._drag_moved = False
        self._canvas.bind("<Button-1>", self._on_fan_press)
        self._canvas.bind("<B1-Motion>", self._on_fan_drag)
        self._canvas.bind("<ButtonRelease-1>", self._on_fan_release)
        self._canvas.bind("<MouseWheel>", self._on_fan_wheel)
        # при первом показе окна размер становится реальным — перерисовать
        self._canvas.bind("<Configure>", lambda _e: self._draw_menu())
        for key in ("<Up>", "<Down>", "<Tab>", "<Return>", "<KP_Enter>", "<space>", "<Escape>"):
            self.root.bind(key, self._main_key)
        self.root.focus_set()
        self._draw_menu()

    def _draw_menu(self):
        if self.menu_style == "radial":
            self._draw_radial()
        else:
            self._draw_fan()

    def _fan_geometry(self, w, h):
        """Центр карусели и параметры дуги под текущий размер окна."""
        return w // 2, int(h * 0.52)

    def _build_frame_photo(self, w, h):
        """Заготовка фоновой картинки больше не используется: рамка рисуется
        векторно на canvas (_draw_frame) — чётко при любом размере окна."""
        return None

    def _draw_frame(self, c, w, h):
        """Рамка удалена: меню — просто текст/круг поверх прозрачности,
        без прямоугольного «витража» и границ окна."""
        return None

    def _animate_fan(self):
        """Плавная доводка позиции карусели к выбранному пункту (easing)."""
        if self.current_screen is not self._canvas or not self._canvas.winfo_exists():
            self._fan_anim_id = None
            return
        diff = self._sel - self._fan_pos
        if abs(diff) < 0.01:
            self._fan_pos = float(self._sel)
            self._fan_anim_id = None
            self._draw_fan()
            return
        self._fan_pos += diff * 0.35
        self._draw_fan()
        self._fan_anim_id = self.root.after(16, self._animate_fan)

    def _fan_redraw_smooth(self):
        if self.menu_style != "fan":
            self._draw_menu()
            return
        if self._fan_anim_id is None:
            self._fan_anim_id = self.root.after(16, self._animate_fan)

    def _draw_fan(self):
        c = self._canvas
        c.delete("all")
        w = self.root.winfo_width() or 760
        h = self.root.winfo_height() or 760
        cx, cy = self._fan_geometry(w, h)
        items = self._items()

        # ── Фон отсутствует: текст парит поверх рабочего стола, без рамки ──
        self._draw_frame(c, w, h)

        # ── Шапка ──
        c.create_text(w / 2, 56, text="⚜ N I M A ⚜", fill=AMBER,
                      font=("Courier New", 20, "bold"))
        c.create_text(w / 2, 84, text=f"✧ NEYRONYA • DEBUG MENU • v{get_version()} ✧",
                      fill=FG, font=("Courier New", 10))
        # переключатель вида меню
        label = "◉ ВИД: КРУГ" if self.menu_style == "fan" else "◉ ВИД: КОЛОННА"
        c.create_text(w / 2, 110, text=label, fill=DIM,
                      font=("Courier New", 9, "bold"), tags="style_toggle")
        self._toggle_bbox = (w / 2 - 110, 96, w / 2 + 110, 124)
        c.create_text(w / 2, h - 18,
                      text="↑↓ / колесо — выбор  •  Enter / клик — открыть  •  тяни — окно",
                      fill=DIM, font=("Courier New", 9))

        # ── Карусель: строго вертикально, ровная колонка по левому краю ──
        pos = self._fan_pos
        x_left = cx - 185
        for i, (label, _action) in enumerate(items):
            delta = i - pos
            if abs(delta) > self._FAN_VISIBLE + 0.6:
                continue
            size = max(9, round(17 * (1 - abs(delta) * self._FAN_SHRINK)))
            is_sel = abs(delta) < 0.25
            color = AMBER if is_sel else (FG if abs(delta) <= 2.2 else DIM)
            y = cy + delta * self._FAN_STEP
            if is_sel:
                c.create_rectangle(x_left - 12, y - size - 7, cx + 205, y + size + 5,
                                   fill="#041704", outline=DIM, width=1)
            c.create_text(x_left, y, text=label, fill=color, anchor="w",
                          font=("Courier New", size, "bold" if is_sel else "normal"),
                          tags=f"item{i}")

    # Радиусные полосы колец (доля от min(w,h)) и цвета сегментов.
    _RAD_HOLE = 0.13       # радиус центральной дырки
    _RAD_R1_OUT = 0.30     # внешний радиус кольца-1 (категории)
    _RAD_R2_IN = 0.315     # внутренний радиус кольца-2 (пункты)
    _RAD_R2_OUT = 0.42     # внешний радиус кольца-2
    _SEG_IDLE = "#2a2f34"
    _SEG_HOVER = "#3a4149"
    _SEG2_IDLE = "#33383d"

    def _draw_radial(self):
        """Радиальный сегментированный вид (донат из двух колец):
        кольцо-1 — категории по кругу; кольцо-2 (появляется по клику) —
        пункты раскрытой категории дугой по ширине её сегмента."""
        import math
        c = self._canvas
        c.delete("all")
        w = self.root.winfo_width() or 760
        h = self.root.winfo_height() or 760
        self._radial_pos = []

        # ── Шапка без фона: только текст поверх прозрачности ──
        c.create_text(w / 2, 46, text="⚜ N I M A ⚜", fill=AMBER,
                      font=("Courier New", 20, "bold"))
        c.create_text(w / 2, 70, text=f"✧ NEYRONYA • DEBUG MENU • v{get_version()} ✧",
                      fill=FG, font=("Courier New", 9))
        label = "◉ ВИД: КОЛОННА" if self.menu_style == "radial" else "◉ ВИД: КРУГ"
        # переключатель — под шапкой, внутри круглого региона окна
        c.create_text(w / 2, 96, text=label, fill=DIM,
                      font=("Courier New", 9, "bold"), tags="style_toggle")
        self._toggle_bbox = (w / 2 - 110, 82, w / 2 + 110, 110)

        cx, cy = w / 2, (70 + h - 30) / 2
        self._radial_center = (cx, cy)
        s = min(w, h)
        r_hole = s * self._RAD_HOLE
        r1_out = s * self._RAD_R1_OUT
        r2_in = s * self._RAD_R2_IN
        r2_out = s * self._RAD_R2_OUT

        cats = self._radial_categories()
        ncat = len(cats)

        def _arc_pieslice(r_out, start_deg, extent_deg, fill, outline="", width=1):
            c.create_arc(cx - r_out, cy - r_out, cx + r_out, cy + r_out,
                         start=start_deg, extent=extent_deg, style="pieslice",
                         fill=fill, outline=outline, width=width)

        def _punch(r):
            # «прорезать» дырку прозрачным цветом окна
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=TRANSPARENT, outline="")

        def _polar(r, ang_rad):
            return cx + r * math.cos(ang_rad), cy + r * math.sin(ang_rad)

        # Углы: в tk 0° справа, положительный extent — против часовой; y вниз,
        # так что на экране это по часовой. Стартуем сверху (90°) и идём по кругу.
        # Кольцо-1: равные секторы под каждую категорию.
        seg = 360.0 / ncat if ncat else 360.0
        cat_arcs = []  # (name, items, flat_base, start_deg, extent_deg)
        for ci, (name, c_items, flat_base) in enumerate(cats):
            start_deg = 90 - (ci + 1) * seg
            cat_arcs.append((name, c_items, flat_base, start_deg, seg))

        # ── Кольцо-2 (рисуем ПЕРВЫМ, чтобы кольцо-1 не затиралось прорезями) ──
        # FIX (UX «второе кольцо слишком узкое»): пункты раскрытой категории
        # занимают широкую дугу ДО 180° по центру сектора категории, а не только
        # ширину самого сектора — иначе в больших категориях сегменты по 5-8°
        # и в них невозможно попасть.
        if self._open_category is not None and 0 <= self._open_category < ncat:
            name, c_items, flat_base, cstart, cext = cat_arcs[self._open_category]
            m = len(c_items)
            if m > 0:
                r2_ext = max(cext, min(180.0, cext * 3.0))
                r2_mid = cstart + cext / 2
                r2_start = r2_mid - r2_ext / 2
                sub = r2_ext / m
                for j, (lbl, action) in enumerate(c_items):
                    a0 = r2_start + j * sub
                    is_sel = (flat_base + j) == self._sel
                    fill = self._SEG_HOVER if is_sel else self._SEG2_IDLE
                    _arc_pieslice(r2_out, a0, sub, fill,
                                  outline=AMBER if is_sel else BG,
                                  width=2 if is_sel else 1)
                    self._radial_pos.append(("item", flat_base + j,
                                             a0, a0 + sub, r2_in, r2_out))
                # прорезать внутренний радиус кольца-2
                _punch(r2_in)
                # иконки + подписи на кольце-2
                r_mid2 = (r2_in + r2_out) / 2
                for j, (lbl, action) in enumerate(c_items):
                    a0 = r2_start + j * sub
                    mid = math.radians(-(a0 + sub / 2))  # экранный угол (y вниз)
                    ix, iy = _polar(r_mid2, mid)
                    icon = self.ICONS.get(action, "•")
                    c.create_text(ix, iy - 7, text=icon, fill=AMBER,
                                  font=("Courier New", 15, "bold"))
                    cap = self._short_caption(lbl)
                    c.create_text(ix, iy + 9, text=cap, fill=FG,
                                  font=("Courier New", 7, "bold"))

        # ── Кольцо-1: категории ──
        for ci, (name, c_items, flat_base, start_deg, ext) in enumerate(cat_arcs):
            is_open = (ci == self._open_category)
            fill = self._SEG_HOVER if is_open else self._SEG_IDLE
            _arc_pieslice(r1_out, start_deg, ext, fill,
                          outline=AMBER if is_open else BG,
                          width=2 if is_open else 1)
            self._radial_pos.append(("cat", ci, start_deg, start_deg + ext,
                                     r_hole, r1_out))
        # прорезать центр (дырка кольца-1)
        _punch(r_hole)

        # подписи категорий по центру их сегмента
        r_mid1 = (r_hole + r1_out) / 2
        for ci, (name, c_items, flat_base, start_deg, ext) in enumerate(cat_arcs):
            mid = math.radians(-(start_deg + ext / 2))
            tx, ty = _polar(r_mid1, mid)
            is_open = (ci == self._open_category)
            c.create_text(tx, ty, text=name, fill=AMBER if is_open else FG,
                          font=("Courier New", 8, "bold"), width=int(r1_out - r_hole))

        # ── Центр: тонкая тёмная граница дырки + имя открытой категории ──
        c.create_oval(cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole,
                      outline=DIM, width=1)
        if self._open_category is not None and 0 <= self._open_category < ncat:
            c.create_text(cx, cy, text=cat_arcs[self._open_category][0],
                          fill=AMBER, font=("Courier New", 9, "bold"),
                          width=int(r_hole * 1.7))

        c.create_text(w / 2, h - 100,
                      text="клик по сектору — открыть  •  клик по центру — закрыть  •  тяни — окно",
                      fill=DIM, font=("Courier New", 9))

    def _item_at(self, y_pos):
        w = self.root.winfo_width() or 760
        h = self.root.winfo_height() or 760
        _cx, cy = self._fan_geometry(w, h)
        band = (self._FAN_VISIBLE + 0.4) * self._FAN_STEP
        if abs(y_pos - cy) > band:
            return None
        delta = round((y_pos - cy) / self._FAN_STEP)
        idx = self._sel + delta
        if abs(delta) > self._FAN_VISIBLE or not (0 <= idx < len(self._items())):
            return None
        return idx

    def _on_fan_press(self, event):
        self._drag_start = (event.x, event.y)
        self._drag_moved = False

    def _on_fan_drag(self, event):
        # Перетаскивание окна за любое место рамки/фона (панели заголовка нет).
        if self._drag_start is None:
            return
        dx, dy = event.x - self._drag_start[0], event.y - self._drag_start[1]
        if abs(dx) + abs(dy) > 4:
            self._drag_moved = True
        if self._drag_moved:
            self.root.geometry(f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}")

    def _on_fan_release(self, event):
        try:
            if self._drag_moved:
                return
            # переключатель вида меню
            if self._toggle_bbox:
                x0, y0, x1, y1 = self._toggle_bbox
                if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                    self._toggle_menu_style()
                    return
            if self.menu_style == "radial":
                self._radial_click(event)
                return
            idx = self._item_at(event.y)
            if idx is not None:
                self._sel = idx
                self._fan_redraw_smooth()
                self._activate()
        finally:
            self._drag_start = None
            self._drag_moved = False

    def _radial_click(self, event):
        """Hit-тест по углу+радиусу для радиального сегментированного меню."""
        import math
        if not self._radial_center:
            return
        cx, cy = self._radial_center
        dx, dy = event.x - cx, event.y - cy
        dist = math.hypot(dx, dy)
        # экранный угол в градусах, приведённый к системе tk (0° справа, CCW).
        ang = (-math.degrees(math.atan2(dy, dx))) % 360.0

        def _in_arc(a, a0, a1):
            a0m, a1m = a0 % 360.0, a1 % 360.0
            am = a % 360.0
            if a0m <= a1m:
                return a0m <= am <= a1m
            return am >= a0m or am <= a1m

        s = min(self.root.winfo_width() or 760, self.root.winfo_height() or 760)
        r_hole = s * self._RAD_HOLE

        # клик по центральной дырке — закрыть категорию
        if dist < r_hole:
            self._open_category = None
            self._draw_menu()
            return

        # сначала пункты (кольцо-2), затем категории (кольцо-1)
        for kind in ("item", "cat"):
            for rec in self._radial_pos:
                r_kind, idx, a0, a1, r_in, r_out = rec
                if r_kind != kind:
                    continue
                if not (r_in <= dist <= r_out):
                    continue
                if not _in_arc(ang, a0, a1):
                    continue
                if kind == "cat":
                    self._open_category = None if self._open_category == idx else idx
                    self._draw_menu()
                    return
                else:
                    self._sel = idx
                    self._activate()
                    return

    def _on_fan_wheel(self, event):
        step = -1 if event.delta > 0 else 1
        self._sel = (self._sel + step) % len(self._items())
        self._fan_redraw_smooth()
        return "break"

    def _main_key(self, event):
        items = self._items()
        if event.keysym in ("Up",):
            self._sel = (self._sel - 1) % len(items)
        elif event.keysym in ("Down", "Tab"):
            self._sel = (self._sel + 1) % len(items)
        elif event.keysym in ("Return", "KP_Enter", "space", "Escape"):
            self._activate()
            return
        if self.menu_style == "radial":
            # держим раскрытую категорию соответствующей выбранному пункту
            cats = self._radial_categories()
            for ci, (_name, c_items, flat_base) in enumerate(cats):
                if flat_base <= self._sel < flat_base + len(c_items):
                    self._open_category = ci
                    break
            self._draw_menu()
            return
        self._fan_redraw_smooth()

    def _select(self, idx):
        self._sel = idx
        self._fan_redraw_smooth()
        self._activate()

    def _activate(self):
        action = self._items()[self._sel][1]
        self._dispatch(action)

    def activate_action(self, action):
        """Запуск пункта по имени действия — выбор приходит из Electron-кольца
        (ring_bridge) в главном потоке Tk через root.after."""
        self._dispatch(action)

    def _dispatch(self, action):
        if action == "exit": self.root.destroy()
        elif action == "launch_bot": self._launch_bot()
        elif action == "console": self.show_console()
        elif action == "stop_bot": self._stop_bot()
        elif action == "restart_bot": self._restart_bot()
        elif action == "trace_request": self.show_trace_request()
        elif action == "trace_tts": self.show_trace_tts()
        elif action == "prompt_view": self.show_prompt_view()
        elif action == "manual_input": self.show_manual_input()
        elif action == "voice_enroll": self.show_voice_enroll()
        elif action == "donation_test": self.show_donation_test()
        elif action == "banwords": self.show_banwords()
        elif action == "dialog_history": self.show_dialog_history()
        elif action == "smoke_test": self.show_smoke_test()
        elif action == "save": self.show_save()
        elif action == "restore": self.show_restore()
        elif action == "test": self.show_test()
        elif action == "logs": self.show_logs()
        elif action == "notes": self.show_notes()
        elif action == "playground": self.show_playground()
        elif action == "full_test": self.show_full_test()
        elif action == "quiz": self.show_quiz()
        elif action == "twitch_quiz": self.show_twitch_quiz()
        elif action == "generated_phrases_review": self.show_generated_phrases_review()
        elif action == "bug_reports": self.show_bug_reports()
        elif action == "task_board": self.show_task_board()
        elif action == "errors": self.show_errors()
        elif action == "phone": self.show_phone()

    def _launch_bot(self):
        """Запуск основного приложения (start.bat) БЕЗ отдельного консольного окна.

        Процесс отслеживается модулем runtime_console, поэтому кнопка
        [ ■ ВЫКЛЮЧИТЬ ПРОЕКТ ] гарантированно его находит; вывод смотрится
        в [ КОНСОЛЬ ] или logs/technical_context.log.
        """
        from tkinter import messagebox
        from .runtime_console import launch_project
        messagebox.showinfo("Запуск", launch_project())

    def _stop_bot(self):
        """Останавливает основной проект: дерево запущенного из меню процесса
        + любые процессы start.py (запуск двойным кликом по start.bat)."""
        from tkinter import messagebox
        from .runtime_console import stop_project
        messagebox.showinfo("Остановка проекта", stop_project())

    def show_main_menu(self):
        for key in ("<Up>", "<Down>", "<Tab>", "<Return>", "<KP_Enter>", "<space>", "<Escape>"):
            try: self.root.unbind(key)
            except Exception: pass
        self._open_category = None
        self._build_main()
        if self.ring:
            # главное меню рисует Electron-кольцо — Tk-окно прячем целиком
            self.ring.visible = True
            self.root.withdraw()

    def show_save(self): self._switch_to(SaveScreen(self.root, self))
    def show_restore(self): self._switch_to(RestoreScreen(self.root, self))
    def show_test(self): self._switch_to(TestScreen(self.root, self))
    def show_logs(self): self._switch_to(LogScreen(self.root, self))
    def show_notes(self): self._switch_to(NotesScreen(self.root, self))
    def show_playground(self): self._switch_to(PlaygroundScreen(self.root, self))

    def show_full_test(self):
        from .full_test import FullTestScreen
        self._switch_to(FullTestScreen(self.root, self))
    def show_quiz(self):
        from .quiz import QuizScreen
        self._switch_to(QuizScreen(self.root, self))

    def show_twitch_quiz(self):
        from .twitch_quiz import show_twitch_quiz
        self._switch_to(show_twitch_quiz(self.root, self))

    def show_generated_phrases_review(self):
        from .generated_phrases_review import show_generated_phrases_review
        self._switch_to(show_generated_phrases_review(self.root, self))

    def show_bug_reports(self):
        self._switch_to(BugReportsScreen(self.root, self))

    def show_console(self):
        from .runtime_console import RuntimeConsoleScreen
        self._switch_to(RuntimeConsoleScreen(self.root, self))

    def show_smoke_test(self):
        from .smoke_test import SmokeTestScreen
        self._switch_to(SmokeTestScreen(self.root, self))

    def show_task_board(self):
        self._switch_to(TaskBoardScreen(self.root, self))

    def show_errors(self):
        from .error_viewer import ErrorViewerScreen
        self._switch_to(ErrorViewerScreen(self.root, self))

    def _restart_bot(self):
        """Быстрый рестарт проекта: стоп → пауза → старт (в фоне, без окна)."""
        from tkinter import messagebox
        from .runtime_console import restart_project
        messagebox.showinfo("Рестарт проекта", restart_project())

    # --- ИНСТРУМЕНТЫ разработчика (возврат из v6.29.0) ---
    def show_trace_request(self):
        from .dev_tools import RequestTraceScreen
        self._switch_to(RequestTraceScreen(self.root, self))

    def show_trace_tts(self):
        from .dev_tools import TtsTraceScreen
        self._switch_to(TtsTraceScreen(self.root, self))

    def show_prompt_view(self):
        from .dev_tools import PromptScreen
        self._switch_to(PromptScreen(self.root, self))

    def show_manual_input(self):
        from .dev_tools import ManualInputScreen
        self._switch_to(ManualInputScreen(self.root, self))

    def show_voice_enroll(self):
        from .dev_tools import VoiceEnrollScreen
        self._switch_to(VoiceEnrollScreen(self.root, self))

    def show_donation_test(self):
        from .dev_tools import DonationTestScreen
        self._switch_to(DonationTestScreen(self.root, self))

    def show_phone(self):
        from .phone import PhoneScreen
        self._switch_to(PhoneScreen(self.root, self))

    def show_banwords(self):
        from .dev_tools import BanwordsScreen
        self._switch_to(BanwordsScreen(self.root, self))

    def show_dialog_history(self):
        from .dev_tools import DialogHistoryScreen
        self._switch_to(DialogHistoryScreen(self.root, self))

    def _switch_to(self, screen):
        if self.current_screen: self.current_screen.destroy()
        self.current_screen = screen
        set_window_shape(self.root, "rect")
        if self.ring:
            # экран Tk заменяет кольцо: Electron-окно прячется, Tk показывается
            self.ring.visible = False
            self.root.deiconify()
        # withdraw/deiconify возвращает окну системные стили — срезаем заново
        strip_window_frame(self.root)
        screen.pack(fill="both", expand=True, padx=16, pady=12)
        for key in ("<Up>", "<Down>", "<Tab>", "<Return>", "<KP_Enter>", "<space>", "<Escape>"):
            try: self.root.unbind(key)
            except Exception: pass


class PlaygroundScreen(BaseScreen):
    """Объединённый экран: три столбика (АНИМАЦИЯ | ОДЕЖДА | ЭМОЦИЯ).

    Каждый столбик — свой список + кнопка запуска, чтобы сразу видеть, работает
    ли всё корректно, не переключаясь между отдельными экранами. 3D-превью
    открывается автоматически рядом (Electron/VRM внутрь Tkinter не встроить).
    """

    def __init__(self, master, app):
        super().__init__(master, app)
        self._init_keyboard_nav()
        self._animations = list(ANIMATIONS)
        self._emotions = list(EMOTIONS)
        self._outfits = []  # (filename, display_name)
        self._preview_auto_opened = False
        self._build()
        self.after(250, self._auto_open_avatar_preview)

    # ── построение ──────────────────────────────────────────────
    def _build(self):
        self._make_header("ПЕСОЧНИЦА НИМФЕИ")
        tk.Label(self, text="Три столбика: анимация · одежда · эмоция. Выбери в любом и нажми кнопку под ним.",
                 bg=BG, fg=AMBER, font=FONT_SM, wraplength=620, justify="left").pack(anchor="w", pady=(0, 6))

        cols = tk.Frame(self, bg=BG); cols.pack(fill="both", expand=True)
        cols.columnconfigure(0, weight=1); cols.columnconfigure(1, weight=1); cols.columnconfigure(2, weight=1)

        # Столбик 1 — АНИМАЦИЯ
        self.lb_anim = self._make_column(cols, 0, "АНИМАЦИЯ", "[ ЗАПУСТИТЬ ]", self._trigger_animation)
        for action, label in self._animations:
            self.lb_anim.insert(tk.END, f" {label} [{action}]")
        if self._animations: self.lb_anim.selection_set(0)

        # Столбик 2 — ОДЕЖДА
        self.lb_outfit = self._make_column(cols, 1, "ОДЕЖДА", "[ НАДЕТЬ ]", self._apply_outfit)
        self._load_outfits()

        # Столбик 3 — ЭМОЦИЯ
        self.lb_emotion = self._make_column(cols, 2, "ЭМОЦИЯ", "[ ПРИМЕНИТЬ ]", self._apply_emotion)
        for mood, label in self._emotions:
            self.lb_emotion.insert(tk.END, f" {label} [{mood}]")
        if self._emotions: self.lb_emotion.selection_set(0)

        bottom = tk.Frame(self, bg=BG); bottom.pack(pady=(6, 2))
        self.btn_open = tk.Button(bottom, text="[ ОТКРЫТЬ 3D ]", width=18, bg=BG, fg=FG, font=FONT,
                                  activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                                  command=self._open_avatar)
        self.btn_open.grid(row=0, column=0, padx=6)

        self.status_var = tk.StringVar(value="3D-превью открывается автоматически рядом. Управление анимацией/одеждой/эмоцией — здесь.")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG, font=FONT_SM, wraplength=620, justify="left").pack(pady=(4, 0))

        self._make_back_button()
        self._register_focusable(self.lb_anim, self._trigger_animation, arrow_nav=False)
        self._register_focusable(self.lb_outfit, self._apply_outfit, arrow_nav=False)
        self._register_focusable(self.lb_emotion, self._apply_emotion, arrow_nav=False)
        self._register_focusable(self.btn_open, self._open_avatar)
        self._focus_widget(0)

    def _make_column(self, parent, col, title, btn_text, command):
        wrap = tk.Frame(parent, bg=BG); wrap.grid(row=0, column=col, sticky="nsew", padx=6)
        tk.Label(wrap, text=title, bg=BG, fg=AMBER, font=FONT).pack(anchor="w")
        list_frame = tk.Frame(wrap, bg=BG); list_frame.pack(fill="both", expand=True, pady=(2, 4))
        sb = tk.Scrollbar(list_frame, orient="vertical")
        lb = tk.Listbox(list_frame, bg=BG, fg=FG, font=FONT_SM, selectbackground=AMBER, selectforeground=BG,
                        height=13, yscrollcommand=sb.set, relief="flat", borderwidth=0, exportselection=False)
        sb.config(command=lb.yview); lb.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        lb.bind("<Double-Button-1>", lambda e, c=command: c())
        lb.bind("<Return>", lambda e, c=command: c())
        lb.bind("<KP_Enter>", lambda e, c=command: c())
        tk.Button(wrap, text=btn_text, bg=BG, fg=AMBER, font=FONT, activebackground=DIM, activeforeground=AMBER,
                  relief="flat", cursor="hand2", command=command).pack(pady=(0, 2))
        return lb

    # ── данные ──────────────────────────────────────────────────
    def _load_outfits(self):
        self.lb_outfit.delete(0, tk.END); self._outfits = []
        try:
            files = sorted(VITA_MODEL_DIR.glob("*.vrm")) if VITA_MODEL_DIR.exists() else []
        except Exception:
            files = []
        if files:
            for f in files:
                self._outfits.append((f.name, _OUTFIT_NAMES.get(f.name, f.stem)))
        else:
            for fname, dname in _OUTFIT_NAMES.items():
                self._outfits.append((fname, dname))
        current = ""
        try:
            import json
            state_path = PROJECT_ROOT / "avatar" / "avatar_state.json"
            if state_path.exists():
                current = json.loads(state_path.read_text(encoding="utf-8")).get("current_outfit", "")
        except Exception:
            pass
        for fname, dname in self._outfits:
            marker = " ◄" if fname == current else ""
            self.lb_outfit.insert(tk.END, f" {dname}{marker}")
        if self._outfits: self.lb_outfit.selection_set(0)

    def _mood_for_action(self, action):
        if action == "thinking": return "thinking"
        if action.startswith("boredom_"): return "boredom"
        return "normal"

    # ── действия ────────────────────────────────────────────────
    def _auto_open_avatar_preview(self):
        if self._preview_auto_opened:
            return
        self._preview_auto_opened = True
        self._open_avatar(auto=True)

    def _trigger_animation(self):
        sel = self.lb_anim.curselection()
        if not sel: self.status_var.set("Выбери анимацию в левом столбике."); return
        action, label = self._animations[sel[0]]; mood = self._mood_for_action(action)
        try:
            import avatar.bridge as animation_integration
            if action == "jump":
                animation_integration.jump()
            else:
                animation_integration.command_avatar(mood=mood, action=action)
            self.status_var.set(f"Анимация «{label}» отправлена. Если 3D закрыто — нажми [ ОТКРЫТЬ 3D ].")
        except Exception as e:
            self.status_var.set(f"Ошибка запуска анимации «{label}»: {e}")

    def _apply_outfit(self):
        sel = self.lb_outfit.curselection()
        if not sel: self.status_var.set("Выбери наряд в среднем столбике."); return
        fname, dname = self._outfits[sel[0]]
        try:
            from avatar.bridge import switch_outfit
            if switch_outfit(fname):
                self.status_var.set(f"Наряд «{dname}» применён. Аватар обновится автоматически.")
                self._load_outfits()
            else:
                self.status_var.set(f"Не удалось применить наряд «{dname}»: VRM-файл не найден или пуст.")
        except Exception as e:
            self.status_var.set(f"Ошибка смены наряда: {e}")

    def _apply_emotion(self):
        sel = self.lb_emotion.curselection()
        if not sel: self.status_var.set("Выбери эмоцию в правом столбике."); return
        mood, label = self._emotions[sel[0]]
        try:
            import avatar.bridge as animation_integration
            # Настроение показываем на нейтральном ожидании, чтобы был виден именно mood,
            # а не текущая one-shot анимация.
            animation_integration.command_avatar(mood=mood, action="idle")
            self.status_var.set(f"Эмоция «{label}» [{mood}] применена. Смотри выражение/позу в 3D.")
        except Exception as e:
            self.status_var.set(f"Ошибка применения эмоции «{label}»: {e}")

    def _open_avatar(self, auto=False):
        sel = self.lb_anim.curselection()
        action = self._animations[sel[0]][0] if sel else "idle"
        mood = self._mood_for_action(action)
        try:
            from avatar.bridge import launch_avatar
            launch_avatar(mood=mood, action=action)
            if auto:
                self.status_var.set("3D-превью Нимфеи открыто рядом с меню. Управление тремя столбиками — здесь.")
            else:
                self.status_var.set("3D-окно Нимфеи открыто.")
        except Exception as e:
            self.status_var.set(f"Ошибка открытия 3D-окна: {e}")


class SaveScreen(BaseScreen):
    def __init__(self, master, app):
        super().__init__(master, app); self._init_keyboard_nav(); self._build()

    def _build(self):
        self._make_header("VAULT-TEC SAVE-O-MATIC")
        tk.Label(self, text="Описание (авто-имя из последних обновлений):", bg=BG, fg=FG, font=FONT).pack(anchor="w")
        desc_frame = tk.Frame(self, bg=BG); desc_frame.pack(fill="x", pady=(2, 10))
        self.desc_entry = tk.Entry(desc_frame, width=30, bg=DIM, fg=AMBER, insertbackground=AMBER, font=FONT)
        self.desc_entry.pack(side="left", fill="x", expand=True)
        self.btn_autoname = tk.Button(desc_frame, text="[ авто-имя ]", width=12, bg=BG, fg=FG, font=FONT,
                                       activebackground=DIM, activeforeground=AMBER, relief="flat",
                                       cursor="hand2", command=self._auto_description)
        self.btn_autoname.pack(side="left", padx=(6, 0))
        self._auto_description()
        tk.Label(self, text="Существующие сохранения:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w")
        list_frame = tk.Frame(self, bg=BG); list_frame.pack(fill="x", pady=(2, 10))
        sb = tk.Scrollbar(list_frame, orient="vertical")
        self.backup_list = tk.Listbox(list_frame, bg=DIM, fg=FG, font=FONT_SM, selectbackground=AMBER, selectforeground=BG, height=6, yscrollcommand=sb.set)
        sb.config(command=self.backup_list.yview); self.backup_list.pack(side="left", fill="x", expand=True); sb.pack(side="right", fill="y")
        self._refresh_backup_list()
        btn_frame = tk.Frame(self, bg=BG); btn_frame.pack(pady=4)
        self.btn_myenv = tk.Button(btn_frame, text="[ + myenv ]", width=14, bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=lambda: self._start_save(True))
        self.btn_myenv.grid(row=0, column=0, padx=6)
        self.btn_unmyenv = tk.Button(btn_frame, text="[ - myenv ]", width=14, bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=lambda: self._start_save(False))
        self.btn_unmyenv.grid(row=0, column=1, padx=6)
        self.btn_inc = tk.Button(btn_frame, text="[ инкремент ]", width=14, bg=BG, fg=FG, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._start_incremental)
        self.btn_inc.grid(row=0, column=2, padx=6)
        tk.Label(self, text="инкремент — частичное сохранение (не полный проект): только файлы, изменённые\nили добавленные с последнего сохранения; удалённые записываются в манифест.",
                 bg=BG, fg=AMBER, font=FONT_SM, justify="left").pack(anchor="w")
        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(self, variable=self.progress_var, maximum=100, mode="determinate", length=340); self.progress_bar.pack(pady=(10, 4))
        self.status_var = tk.StringVar(value="Готов к сохранению")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG, font=FONT_SM, wraplength=360, justify="left").pack(pady=(0, 4))
        self._make_back_button()
        self._register_focusable(self.desc_entry)
        self._register_focusable(self.btn_myenv, lambda: self._start_save(True))
        self._register_focusable(self.btn_unmyenv, lambda: self._start_save(False))
        self._register_focusable(self.btn_inc, self._start_incremental)
        self._register_focusable(self.btn_autoname, self._auto_description)
        self._focus_widget(0)

    def _auto_description(self):
        """Авто-имя сохранения из последних обновлений: версия + заголовок
        последней записи CHANGELOG. sanitize_description оставляет только 2
        «слова», поэтому описание сшиваем дефисами в одно слово — иначе оно
        не доживает до имени архива."""
        try:
            text = CHANGELOG_PATH.read_text(encoding="utf-8", errors="ignore")
            title = ""
            for line in text.splitlines():
                if line.startswith("## "):
                    t = re.sub(r"\((?:🔴|🟢|🟠|🟡|⬛)\)\s*$", "", line[3:]).strip()
                    t = re.sub(r"^(?:Нимфе[яи]|Vita)\s*v[\d.]+\s*", "", t, flags=re.IGNORECASE)
                    t = re.sub(r"[^\wА-Яа-яЁё ]", " ", t)
                    _stop = {"на", "в", "и", "с", "из", "под", "от", "до", "по", "за", "у", "о"}
                    words = [w for w in t.split() if w and w.lower() not in _stop][:6]
                    title = "-".join(words).lower()
                    break
            # точки в версии sanitize_description превращает в разрыв слова —
            # дефисы доживают до имени архива целиком; «v» не дублируем
            # (get_version уже возвращает «v14.8.48», урок 14.09: было «vv14-8-48»)
            ver = get_version().replace(".", "-")
            desc = f"{ver if ver.startswith('v') else 'v' + ver} {title}".strip() or "backup"
            self.desc_entry.delete(0, tk.END)
            self.desc_entry.insert(0, desc[:60])
            if hasattr(self, "status_var"):
                self.status_var.set("Имя подставлено из последней записи CHANGELOG.")
        except Exception as e:
            self.desc_entry.delete(0, tk.END); self.desc_entry.insert(0, "backup")
            if hasattr(self, "status_var"):
                self.status_var.set(f"Авто-имя не удалось: {e}")

    def _refresh_backup_list(self):
        self.backup_list.delete(0, tk.END)
        for f in find_backups():
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            self.backup_list.insert(tk.END, f"  {f.name}  [{mtime}]")
        if self.backup_list.size() > 0: self.backup_list.see(tk.END)

    def _set_buttons(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.btn_myenv.config(state=state); self.btn_unmyenv.config(state=state)
        self.btn_inc.config(state=state); self.desc_entry.config(state=state)

    def _update_progress(self, text, value=None, mode=None):
        def apply():
            if mode:
                if self.progress_bar["mode"] == "indeterminate" and mode != "indeterminate": self.progress_bar.stop()
                self.progress_bar.config(mode=mode)
                if mode == "indeterminate": self.progress_bar.start(12)
            if value is not None and self.progress_bar["mode"] == "determinate": self.progress_var.set(value)
            self.status_var.set(text)
        try: self.after(0, apply)
        except Exception: pass

    def _start_save(self, include_myenv):
        self._set_buttons(False); self.progress_var.set(0); self.status_var.set("Запуск архивации...")
        threading.Thread(target=self._save_worker, args=(include_myenv, self.desc_entry.get()), daemon=True).start()

    def _start_incremental(self):
        self._set_buttons(False); self.progress_var.set(0); self.status_var.set("Запуск частичного сохранения...")
        threading.Thread(target=self._inc_worker, args=(self.desc_entry.get(),), daemon=True).start()

    def _save_worker(self, include_myenv, description):
        try: path = make_archive(include_myenv, description, self._update_progress)
        except Exception as e:
            err = str(e)  # e удаляется при выходе из except — фиксируем значение для lambda
            self.after(0, lambda: self._finish(error=err))
        else: self.after(0, lambda: self._finish(archive_path=path))

    def _inc_worker(self, description):
        try: path, changed, deleted = make_incremental_archive(description, self._update_progress)
        except Exception as e:
            err = str(e)  # e удаляется при выходе из except — фиксируем значение для lambda
            self.after(0, lambda: self._finish(error=err))
        else:
            summary = f"изменено: {changed}, удалено: {deleted}"
            self.after(0, lambda: self._finish(archive_path=path, summary=summary))

    def _finish(self, archive_path=None, error=None, summary=None):
        self.progress_bar.stop(); self.progress_bar.config(mode="determinate"); self._set_buttons(True)
        if error:
            self.progress_var.set(0); self.status_var.set(f"Ошибка: {error}"); messagebox.showerror("Ошибка", error)
        else:
            self.progress_var.set(100); archive_display = str(archive_path)
            status = f"Создан: {archive_path.name}" + (f" ({summary})" if summary else "")
            self.status_var.set(status); self._refresh_backup_list()
            messagebox.showinfo("Готово", f"Архив создан:\n{archive_display}\n\n" + (f"Частичное сохранение ({summary})." if summary else "Полное сохранение проекта."))


class RestoreScreen(BaseScreen):
    def __init__(self, master, app):
        super().__init__(master, app); self._init_keyboard_nav(); self._backups = []; self._build()

    def _build(self):
        self._make_header("VAULT-TEC RESTORE")
        tk.Label(self, text="Выберите версию для восстановления:", bg=BG, fg=FG, font=FONT).pack(anchor="w")
        tk.Label(self, text="(↑↓ — навигация, Enter — восстановить)", bg=BG, fg=AMBER, font=FONT_SM).pack(anchor="w", pady=(0, 6))
        list_frame = tk.Frame(self, bg=BG); list_frame.pack(fill="x")
        sb = tk.Scrollbar(list_frame, orient="vertical")
        self.lb = tk.Listbox(list_frame, bg=DIM, fg=FG, font=FONT_SM, selectbackground=AMBER, selectforeground=BG, height=10, yscrollcommand=sb.set)
        sb.config(command=self.lb.yview); self.lb.pack(side="left", fill="x", expand=True); sb.pack(side="right", fill="y")
        self._load_backups(); self.lb.bind("<Double-Button-1>", lambda e: self._confirm_restore()); self.lb.bind("<Return>", lambda e: self._confirm_restore()); self.lb.bind("<KP_Enter>", lambda e: self._confirm_restore()); self.lb.focus_set()
        self.progress_var = tk.IntVar(value=0); self.progress_bar = ttk.Progressbar(self, variable=self.progress_var, maximum=100, mode="determinate", length=340); self.progress_bar.pack(pady=(10, 4))
        self.status_var = tk.StringVar(value=""); tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG, font=FONT_SM, wraplength=360, justify="left").pack(pady=(0, 4))
        btn_frame = tk.Frame(self, bg=BG); btn_frame.pack(pady=4)
        self.btn_restore = tk.Button(btn_frame, text="[ RESTORE ]", width=14, bg=BG, fg=AMBER, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._confirm_restore)
        self.btn_restore.grid(row=0, column=0, padx=6); self._make_back_button()
        self._register_focusable(self.lb, self._confirm_restore, arrow_nav=False)
        self._register_focusable(self.btn_restore, self._confirm_restore)
        self._focus_widget(0)

    def _load_backups(self):
        self.lb.delete(0, tk.END); self._backups = find_backups()
        if not self._backups:
            self.lb.insert(tk.END, "  (нет сохранений в B:\\)"); return
        for f in self._backups:
            mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
            if " INC " in f.name:
                tag = " [INC]"
            elif "UN" not in f.name.upper() and "UM" not in f.name.upper():
                tag = " [+myenv]"
            else:
                tag = " [-myenv]"
            self.lb.insert(tk.END, f"  {f.name}{tag}  [{mtime}]")
        last = len(self._backups) - 1; self.lb.selection_set(last); self.lb.see(last)

    def _confirm_restore(self):
        if not self._backups: return
        idx = self.lb.curselection()
        if not idx: return
        archive = self._backups[idx[0]]
        if " INC " in archive.name:
            self._confirm_restore_incremental(archive)
            return
        answer = messagebox.askyesno("Подтверждение", f"Восстановить проект из:\n{archive.name}\n\nВНИМАНИЕ: текущие файлы будут удалены!\nФайлы дебаг-меню будут сохранены.\n\nПродолжить?")
        if not answer: return
        self._run_restore(archive, is_inc=False)

    def _confirm_restore_incremental(self, archive):
        try: manifest = read_incremental_manifest(archive)
        except Exception: manifest = None
        changed = list((manifest or {}).get("changed", []))
        deleted = list((manifest or {}).get("deleted", []))
        saved_version = (manifest or {}).get("version") or version_from_name(archive.name) or "?"
        current_version = get_version()
        lines = [
            "ЧАСТИЧНОЕ СОХРАНЕНИЕ (не полный проект).",
            f"Версия сохранения: {saved_version}   |   Текущая версия проекта: {current_version}",
        ]
        if saved_version != current_version:
            lines.append("ВНИМАНИЕ: версии не совпадают!")
        lines.append(f"Файлов в сохранении: {len(changed)}, удалено на момент сохранения: {len(deleted)}")
        if changed:
            lines.append("Сохранённые файлы:")
            lines.extend(f"  {rel}" for rel in changed[:8])
            if len(changed) > 8:
                lines.append(f"  …и ещё {len(changed) - 8}")
        lines += [
            "",
            "Текущие версии этих файлов будут удалены и заменены версиями из сохранения;",
            "файлы, удалённые на момент сохранения, будут удалены и из проекта.",
            "Остальные файлы проекта не затрагиваются.",
            "",
            f"Восстановить из:\n{archive.name}?",
        ]
        if not messagebox.askyesno("Частичное восстановление", "\n".join(lines)): return
        self._run_restore(archive, is_inc=True)

    def _run_restore(self, archive, is_inc):
        self.btn_restore.config(state=tk.DISABLED); self.progress_var.set(0)
        self.status_var.set("Запуск частичного восстановления..." if is_inc else "Запуск восстановления...")
        threading.Thread(target=self._restore_worker, args=(archive, is_inc), daemon=True).start()

    def _update_progress(self, text, value=None, mode=None):
        def apply():
            if mode:
                if self.progress_bar["mode"] == "indeterminate" and mode != "indeterminate": self.progress_bar.stop()
                self.progress_bar.config(mode=mode)
                if mode == "indeterminate": self.progress_bar.start(12)
            if value is not None and self.progress_bar["mode"] == "determinate": self.progress_var.set(value)
            self.status_var.set(text)
        try: self.after(0, apply)
        except Exception: pass

    def _restore_worker(self, archive, is_inc=False):
        try:
            restore = restore_incremental_archive if is_inc else restore_archive
            restore(archive, self._update_progress)
        except Exception as e:
            err = str(e)  # фиксируем значение: e недоступна после выхода из except
            self.after(0, lambda: self._finish(error=err))
        else: self.after(0, lambda: self._finish(is_inc=is_inc))

    def _finish(self, error=None, is_inc=False):
        self.progress_bar.stop(); self.progress_bar.config(mode="determinate"); self.btn_restore.config(state=tk.NORMAL)
        if error:
            self.progress_var.set(0); self.status_var.set(f"Ошибка: {error}"); messagebox.showerror("Ошибка восстановления", error)
        else:
            self.progress_var.set(100); self.status_var.set("Восстановление завершено!")
            message = ("Частичное восстановление завершено:\nизменённые файлы откатены к состоянию сохранения.\nПерезапустите приложение."
                       if is_inc else "Проект успешно восстановлен!\nПерезапустите приложение.")
            messagebox.showinfo("Готово", message)


class TestScreen(BaseScreen):
    def __init__(self, master, app):
        super().__init__(master, app); self._init_keyboard_nav(); self._build()

    def _build(self):
        self._make_header("MODULE DIAGNOSTICS")
        tk.Label(self, text="Тест импорта всех модулей проекта.", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w")
        tk.Label(self, text="OK = импорт успешен  |  FAIL = ошибка  |  WARN = нет атрибута", bg=BG, fg=AMBER, font=FONT_SM).pack(anchor="w", pady=(0, 6))
        list_frame = tk.Frame(self, bg=BG); list_frame.pack(fill="both", expand=True)
        sb = tk.Scrollbar(list_frame, orient="vertical")
        self.result_list = tk.Listbox(list_frame, bg=DIM, fg=FG, font=FONT_SM, selectbackground=AMBER, selectforeground=BG, height=16, yscrollcommand=sb.set, width=60)
        sb.config(command=self.result_list.yview); self.result_list.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        tk.Label(self, text="Детали ошибки:", bg=BG, fg=FG, font=FONT_SM).pack(anchor="w", pady=(6, 0))
        self.detail_text = tk.Text(self, bg=DIM, fg=RED_ERR, font=FONT_SM, height=4, wrap="word", state="disabled"); self.detail_text.pack(fill="x", pady=(2, 6))
        self.result_list.bind("<<ListboxSelect>>", self._on_select)
        btn_frame = tk.Frame(self, bg=BG); btn_frame.pack(pady=4)
        self.btn_run = tk.Button(btn_frame, text="[ RUN TESTS ]", width=16, bg=BG, fg=AMBER, font=FONT, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._run_tests)
        self.btn_run.grid(row=0, column=0, padx=6)
        self.status_var = tk.StringVar(value="Нажмите RUN TESTS для запуска"); tk.Label(self, textvariable=self.status_var, bg=BG, fg=FG, font=FONT_SM).pack()
        self._make_back_button(); self._errors = {}
        self._register_focusable(self.result_list, arrow_nav=False)
        self._register_focusable(self.btn_run, self._run_tests)
        self._focus_widget(1)

    def _run_tests(self):
        self.btn_run.config(state=tk.DISABLED); self.result_list.delete(0, tk.END); self.status_var.set("Тестирование..."); self._errors = {}
        threading.Thread(target=self._test_worker, daemon=True).start()

    def _test_worker(self):
        results = test_all_modules(); self.after(0, lambda: self._show_results(results))

    def _show_results(self, results):
        ok = sum(1 for _, s, _ in results if s == "OK"); fail = sum(1 for _, s, _ in results if s == "FAIL"); warn = sum(1 for _, s, _ in results if s == "WARN")
        for i, (name, status, err) in enumerate(results):
            if status == "OK": line, color = f"  ✓  {name:<40} OK", FG
            elif status == "WARN": line, color = f"  ⚠  {name:<40} WARN", AMBER
            else: line, color = f"  ✗  {name:<40} FAIL", RED_ERR
            self.result_list.insert(tk.END, line); self.result_list.itemconfig(i, fg=color)
            if err: self._errors[i] = err
        self.status_var.set(f"Результат: {ok} OK  |  {warn} WARN  |  {fail} FAIL  ({'все модули в порядке!' if fail == 0 else 'есть ошибки!'})")
        self.btn_run.config(state=tk.NORMAL)

    def _on_select(self, event):
        sel = self.result_list.curselection()
        if not sel: return
        err = self._errors.get(sel[0], "")
        self.detail_text.config(state="normal"); self.detail_text.delete("1.0", tk.END); self.detail_text.insert(tk.END, err if err else "(нет ошибок)"); self.detail_text.config(state="disabled")


class LogScreen(BaseScreen):
    def __init__(self, master, app):
        super().__init__(master, app); self._init_keyboard_nav(); self._log_files = []; self._build()

    def _build(self):
        self._make_header("LOG VIEWER")
        top_frame = tk.Frame(self, bg=BG); top_frame.pack(fill="x", pady=(0, 6))
        tk.Label(top_frame, text="Файл лога:", bg=BG, fg=FG, font=FONT_SM).pack(side="left")
        self.log_var = tk.StringVar(); self._log_files = self._find_logs(); log_names = [f.name for f in self._log_files]
        self.log_combo = ttk.Combobox(top_frame, textvariable=self.log_var, values=log_names, state="readonly", font=FONT_SM, width=36)
        if log_names: self.log_combo.current(0)
        self.log_combo.pack(side="left", padx=(6, 0)); self.log_combo.bind("<<ComboboxSelected>>", lambda e: self._load_log())
        search_frame = tk.Frame(self, bg=BG); search_frame.pack(fill="x", pady=(0, 4))
        tk.Label(search_frame, text="Поиск:", bg=BG, fg=FG, font=FONT_SM).pack(side="left")
        self.search_var = tk.StringVar(); self.search_entry = tk.Entry(search_frame, textvariable=self.search_var, bg=DIM, fg=AMBER, insertbackground=AMBER, font=FONT_SM, width=28)
        self.search_entry.pack(side="left", padx=(6, 0))
        self.btn_search = tk.Button(search_frame, text="[ НАЙТИ ]", bg=BG, fg=FG, font=FONT_SM, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._search)
        self.btn_search.pack(side="left", padx=(6, 0))
        self.btn_refresh = tk.Button(search_frame, text="[ ОБНОВИТЬ ]", bg=BG, fg=FG, font=FONT_SM, activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2", command=self._load_log)
        self.btn_refresh.pack(side="left", padx=(6, 0))
        text_frame = tk.Frame(self, bg=BG); text_frame.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(text_frame, orient="vertical"); hsb = tk.Scrollbar(text_frame, orient="horizontal")
        self.log_text = tk.Text(text_frame, bg=DIM, fg=FG, font=FONT_SM, wrap="none", state="disabled", yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.config(command=self.log_text.yview); hsb.config(command=self.log_text.xview)
        self.log_text.grid(row=0, column=0, sticky="nsew"); vsb.grid(row=0, column=1, sticky="ns"); hsb.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1); text_frame.columnconfigure(0, weight=1); self.log_text.tag_config("highlight", background=AMBER, foreground=BG)
        self.status_var = tk.StringVar(value=""); tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM).pack(pady=(2, 0))
        self._make_back_button()
        self._register_focusable(self.log_combo)
        self._register_focusable(self.search_entry)
        self._register_focusable(self.btn_search, self._search)
        self._register_focusable(self.btn_refresh, self._load_log)
        self._register_focusable(self.log_text, arrow_nav=False)
        self._focus_widget(4)
        if log_names: self._load_log()

    def _find_logs(self):
        logs_dir = PROJECT_ROOT / "logs"; result = []
        if logs_dir.exists():
            for f in sorted(logs_dir.glob("*.log")): result.append(f)
        result.sort(key=lambda f: (0 if f.name == "technical_context.log" else 1, f.name)); return result

    def _load_log(self):
        name = self.log_var.get()
        if not name: return
        log_file = PROJECT_ROOT / "logs" / name
        self.log_text.config(state="normal"); self.log_text.delete("1.0", tk.END)
        try:
            content = log_file.read_text(encoding="utf-8", errors="replace"); self.log_text.insert(tk.END, content); self.log_text.see(tk.END); self.status_var.set(f"Загружено: {log_file.name}  ({content.count(chr(10))} строк)")
        except Exception as e:
            self.log_text.insert(tk.END, f"Ошибка чтения: {e}"); self.status_var.set(f"Ошибка: {e}")
        self.log_text.config(state="disabled")

    def _search(self):
        query = self.search_var.get().strip()
        if not query: return
        self.log_text.tag_remove("highlight", "1.0", tk.END); start = "1.0"; count = 0; first_pos = None
        while True:
            pos = self.log_text.search(query, start, stopindex=tk.END, nocase=True)
            if not pos: break
            end_pos = f"{pos}+{len(query)}c"; self.log_text.tag_add("highlight", pos, end_pos)
            if first_pos is None: first_pos = pos
            start = end_pos; count += 1
        if first_pos: self.log_text.see(first_pos)
        self.status_var.set(f"Найдено вхождений: {count}" if count else "Не найдено")


class NotesScreen(BaseScreen):
    def __init__(self, master, app):
        super().__init__(master, app); self._init_keyboard_nav(); self._build()

    def _build(self):
        self._make_header("PATCH NOTES")
        tk.Label(self, text="История версий — что и в какой версии добавлено (docs/CHANGELOG.md):", bg=BG, fg=AMBER, font=FONT_SM, wraplength=420, justify="left").pack(anchor="w", pady=(0, 6))
        text_frame = tk.Frame(self, bg=BG); text_frame.pack(fill="both", expand=True)
        vsb = tk.Scrollbar(text_frame, orient="vertical")
        self.notes_text = tk.Text(text_frame, bg=DIM, fg=FG, font=FONT_SM, wrap="word", state="disabled", yscrollcommand=vsb.set)
        vsb.config(command=self.notes_text.yview); self.notes_text.grid(row=0, column=0, sticky="nsew"); vsb.grid(row=0, column=1, sticky="ns")
        text_frame.rowconfigure(0, weight=1); text_frame.columnconfigure(0, weight=1)
        self._load_notes(); self._make_back_button()
        self._register_focusable(self.notes_text, arrow_nav=False)
        self._focus_widget(0)

    def _load_notes(self):
        self.notes_text.config(state="normal"); self.notes_text.delete("1.0", tk.END)
        try: self.notes_text.insert(tk.END, (CHANGELOG_PATH.read_text(encoding="utf-8", errors="replace") if CHANGELOG_PATH.exists() else f"CHANGELOG.md не найден:\n{CHANGELOG_PATH}")); self.notes_text.see("1.0")
        except Exception as e: self.notes_text.insert(tk.END, f"Ошибка чтения CHANGELOG.md ({CHANGELOG_PATH}):\n{e}")
        self.notes_text.config(state="disabled")


_OUTFIT_NAMES = {
    "Nima_drees.vrm": "Платье",
    "Nima_jeens.vrm": "Джинсы",
    "Nima_kimono.vrm": "Кимоно",
    "Nima_maid.vrm": "Горничная",
    "Nima_naked.vrm": "Без одежды",
    "Nima_Sexual.vrm": "Сексуальный образ",
    "Nima_standart.vrm": "Стандарт",
}


class BugReportsScreen(BaseScreen):
    """Экран [ БАГИ ]: заметить баг — запись в docs/BUGS.md с версией и файлами."""

    def __init__(self, master, app):
        super().__init__(master, app)
        self._init_keyboard_nav()
        self._build()
        self._focus_widget(0)

    def _build(self):
        from .bug_reports import BUGS_PATH, get_current_version, get_version_changed_files
        self._make_header("БАГИ: ЗАМЕТИТЬ БАГ")
        version = get_current_version()
        files = get_version_changed_files(version)
        tk.Label(self, text=f"Текущая версия: Vita v{version}", bg=BG, fg=AMBER, font=FONT).pack(pady=(0, 2))
        files_line = ", ".join(files[:8]) if files else "—"
        tk.Label(self, text=f"Файлы, менявшиеся в v{version}:\n{files_line}", bg=BG, fg=DIM, font=FONT_SM,
                 wraplength=560, justify="left").pack(pady=(0, 6), fill="x")
        tk.Label(self, text="Опиши баг (Enter — добавить, стрелки — курсор в поле):", bg=BG, fg=FG, font=FONT_SM).pack()
        self.bug_entry = tk.Text(self, height=4, width=66, bg="#101010", fg=FG, font=FONT_SM,
                                 insertbackground=FG, relief="flat")
        self.bug_entry.pack(pady=(2, 6))
        # Выбор важности и срочности бага (попадают в запись BUGS.md).
        from .bug_reports import IMPORTANCE_LEVELS, URGENCY_LEVELS, DEFAULT_IMPORTANCE, DEFAULT_URGENCY
        self._importance = tk.StringVar(value=DEFAULT_IMPORTANCE)
        self._urgency = tk.StringVar(value=DEFAULT_URGENCY)
        tk.Label(self, text="Важность (насколько критичен):", bg=BG, fg=FG, font=FONT_SM).pack()
        for value, label in IMPORTANCE_LEVELS:
            rb = tk.Radiobutton(self, text=label, variable=self._importance, value=value,
                                bg=BG, fg=FG, selectcolor="#101010", activebackground=BG,
                                activeforeground=AMBER, font=FONT_SM, anchor="w", relief="flat")
            rb.pack(fill="x", padx=60)
            self._register_focusable(rb, arrow_nav=False)
        tk.Label(self, text="Срочность (когда решать):", bg=BG, fg=FG, font=FONT_SM).pack()
        for value, label in URGENCY_LEVELS:
            rb = tk.Radiobutton(self, text=label, variable=self._urgency, value=value,
                                bg=BG, fg=FG, selectcolor="#101010", activebackground=BG,
                                activeforeground=AMBER, font=FONT_SM, anchor="w", relief="flat")
            rb.pack(fill="x", padx=60)
            self._register_focusable(rb, arrow_nav=False)
        self.status_var = tk.StringVar(value="Записи сохраняются в docs/BUGS.md")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM, wraplength=560).pack()
        btn_row = tk.Frame(self, bg=BG); btn_row.pack(pady=4)
        btn_add = tk.Button(btn_row, text="[ ДОБАВИТЬ БАГ ]", bg=BG, fg=FG, font=FONT,
                            activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                            command=self._add_bug)
        btn_add.pack(side="left", padx=6)
        btn_view = tk.Button(btn_row, text="[ ОТКРЫТЬ BUGS.md ]", bg=BG, fg=FG, font=FONT,
                             activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                             command=self._open_file)
        btn_view.pack(side="left", padx=6)
        btn_smoke = tk.Button(btn_row, text="[ СМОК-ТЕСТ ОКНО ]", bg=BG, fg=FG, font=FONT,
                              activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                              command=self._open_smoke_window)
        btn_smoke.pack(side="left", padx=6)
        self._make_back_button()
        self._register_focusable(btn_add, self._add_bug)
        self._register_focusable(btn_view, self._open_file)
        self._register_focusable(btn_smoke, self._open_smoke_window)
        self._register_focusable(self.bug_entry, arrow_nav=False)

    def _add_bug(self):
        from .bug_reports import append_bug
        text = self.bug_entry.get("1.0", "end").strip()
        if not text:
            self.status_var.set("Сначала опиши баг в поле выше.")
            return
        try:
            path = append_bug(text, importance=self._importance.get(), urgency=self._urgency.get())
            self.bug_entry.delete("1.0", "end")
            self.status_var.set(f"Баг зафиксирован в {path.name} (версия, важность и файлы приложены).")
        except Exception as e:
            self.status_var.set(f"Ошибка записи: {e}")

    def _open_file(self):
        from .bug_reports import load_bugs_text
        content = load_bugs_text() or "(пока пусто — зафиксируй первый баг)"
        win = tk.Toplevel(self); win.title("BUGS.md"); win.configure(bg=BG)
        txt = tk.Text(win, bg="#101010", fg=FG, font=FONT_SM, insertbackground=FG)
        txt.insert("1.0", content); txt.pack(fill="both", expand=True, padx=8, pady=8)
        win.geometry("760x480")

    def _open_smoke_window(self):
        from .smoke_test import open_smoke_test_window
        open_smoke_test_window(self)


class TaskBoardScreen(BaseScreen):
    """Экран [ ЗАДАЧИ ]: добавить задачу в docs/TASKS_IN_PROGRESS.md по сложности."""

    LEVELS = (("green", "🟢 Простая (patch 0.0.x)"),
              ("yellow", "🟡 Средняя (minor 0.x.0)"),
              ("red", "🔴 Сложная (major x.0.0)"))

    def __init__(self, master, app):
        super().__init__(master, app)
        self._init_keyboard_nav()
        self._level = tk.StringVar(value="green")
        self._build()
        self._focus_widget(0)

    def _build(self):
        from .task_board import TASKS_PATH, count_tasks
        self._make_header("ЗАДАЧИ: ЧЕК-ЛИСТ")
        counts = count_tasks()
        tk.Label(self, text=f"Открыто: 🟢 {counts.get('green', 0)}   🟡 {counts.get('yellow', 0)}   "
                            f"🔴 {counts.get('red', 0)}   →  {TASKS_PATH.name}", bg=BG, fg=AMBER, font=FONT).pack(pady=(0, 6))
        tk.Label(self, text="Текст новой задачи:", bg=BG, fg=FG, font=FONT_SM).pack()
        self.task_entry = tk.Entry(self, width=64, bg="#101010", fg=FG, font=FONT, insertbackground=FG,
                                   relief="flat")
        self.task_entry.pack(pady=(2, 6))
        tk.Label(self, text="Сложность:", bg=BG, fg=FG, font=FONT_SM).pack()
        for value, label in self.LEVELS:
            rb = tk.Radiobutton(self, text=label, variable=self._level, value=value,
                                bg=BG, fg=FG, selectcolor="#101010", activebackground=BG,
                                 activeforeground=AMBER, font=FONT, anchor="w", relief="flat")
            rb.pack(fill="x", padx=60)
            self._register_focusable(rb, arrow_nav=False)
        self.status_var = tk.StringVar(value="Задача встанет в начало нужного раздела чек-листа.")
        tk.Label(self, textvariable=self.status_var, bg=BG, fg=AMBER, font=FONT_SM, wraplength=560).pack(pady=(4, 2))
        btn_row = tk.Frame(self, bg=BG); btn_row.pack(pady=4)
        btn_add = tk.Button(btn_row, text="[ ДОБАВИТЬ ЗАДАЧУ ]", bg=BG, fg=FG, font=FONT,
                            activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                            command=self._add_task)
        btn_add.pack(side="left", padx=6)
        btn_view = tk.Button(btn_row, text="[ ОТКРЫТЬ ЧЕК-ЛИСТ ]", bg=BG, fg=FG, font=FONT,
                             activebackground=DIM, activeforeground=AMBER, relief="flat", cursor="hand2",
                             command=self._open_file)
        btn_view.pack(side="left", padx=6)
        self._make_back_button()
        self._register_focusable(self.task_entry, arrow_nav=False)
        self._register_focusable(btn_add, self._add_task)
        self._register_focusable(btn_view, self._open_file)

    def _add_task(self):
        from .task_board import add_task, count_tasks
        text = self.task_entry.get().strip()
        if not text:
            self.status_var.set("Сначала напиши текст задачи.")
            return
        try:
            add_task(text, self._level.get())
            counts = count_tasks()
            self.task_entry.delete(0, tk.END)
            self.status_var.set(f"Добавлено. Открыто теперь: 🟢 {counts.get('green', 0)}  "
                                f"🟡 {counts.get('yellow', 0)}  🔴 {counts.get('red', 0)}")
        except Exception as e:
            self.status_var.set(f"Ошибка записи: {e}")

    def _open_file(self):
        from .task_board import load_tasks_text
        content = load_tasks_text() or "(чек-лист не найден)"
        win = tk.Toplevel(self); win.title("TASKS_IN_PROGRESS.md"); win.configure(bg=BG)
        txt = tk.Text(win, bg="#101010", fg=FG, font=FONT_SM, insertbackground=FG)
        txt.insert("1.0", content); txt.pack(fill="both", expand=True, padx=8, pady=8)
        win.geometry("760x480")
