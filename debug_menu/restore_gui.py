import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk
import os
import sys
import subprocess
import threading
import glob
import time
import ctypes
import traceback

# в”Ђв”Ђ Config в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
PROJECT_ROOT = r'B:\Neyronya'
BACKUP_ROOT  = 'B:\\'
WINRAR       = r'B:\Winrar\Rar.exe'
KEEP_FILES   = {'debug_menu', 'debug menu.bat'}  # v12: меню консолидировано в debug_menu/
TEMP_ROOT    = r'B:\temp\backup_restore'

# в”Ђв”Ђ Colors в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
BG        = '#0a0a0a'
SCREEN_BG = '#0d1a0d'
GREEN     = '#00ff41'
GREEN_DIM = '#007a1f'
AMBER     = '#ffb000'
RED       = '#ff3333'
CYAN      = '#00e5ff'
GRAY      = '#4a4a4a'
BORDER    = '#1a3a1a'

FONT_MONO = ('Courier New', 11)
FONT_TITLE = ('Courier New', 10, 'bold')

# в”Ђв”Ђ Helpers в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def hide_console():
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass

def fmt_size(b):
    if b >= 1_073_741_824: return f'{b/1_073_741_824:.1f} GB'
    if b >= 1_048_576:     return f'{b/1_048_576:.1f} MB'
    return f'{b/1024:.0f} KB'

def find_backups():
    files = glob.glob(os.path.join(BACKUP_ROOT, 'Нимфея V*.rar'))
    files += glob.glob(os.path.join(BACKUP_ROOT, 'Vita V*.rar'))
    files = list(dict.fromkeys(files))
    files.sort(key=os.path.getmtime, reverse=True)
    result = []
    for f in files:
        stat = os.stat(f)
        result.append({
            'path': f,
            'name': os.path.basename(f),
            'size': fmt_size(stat.st_size),
            'mtime': time.strftime('%d.%m.%Y  %H:%M', time.localtime(stat.st_mtime))
        })
    return result

def _norm_path(path):
    return os.path.normcase(os.path.abspath(path)).rstrip('\\/')

def is_running_from_project_myenv():
    exe = _norm_path(sys.executable)
    myenv = _norm_path(os.path.join(PROJECT_ROOT, 'myenv'))
    return exe == myenv or exe.startswith(myenv + os.sep)

def describe_busy_myenv_error():
    return (
        'Restore with myenv cannot continue because this GUI is running from '\
        f'{os.path.join(PROJECT_ROOT, "myenv")}. Windows locks python.exe/pythonw.exe '\
        'and loaded DLLs, so the active myenv cannot be deleted or replaced in '\
        'the same process. Close Neyronya/Python processes and start restore.bat '\
        'again so it can use system Python/py launcher, or run restore.ps1 from an '\
        'external PowerShell process.'
    )

# в”Ђв”Ђ Main App в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
class RestoreApp:
    def __init__(self, root):
        self.root = root
        self.root.title('NEYRONYA RESTORE')
        self.root.configure(bg=BG)
        self.root.resizable(False, False)

        self.backups = []
        self.selected_idx = 0   # for GTA-style confirm
        self.confirm_active = False
        self.confirm_choice = 0  # 0=NO, 1=YES

        self._build_console_body()
        self._build_screen()
        self._build_buttons()

        self.root.bind('<Key>', self._on_key)
        self.root.after(100, self._start_scan)

    # в”Ђв”Ђ Layout в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _build_console_body(self):
        # Outer shell вЂ” dark plastic
        self.body = tk.Frame(self.root, bg='#1a1a1a', bd=0,
                             highlightbackground='#333', highlightthickness=3)
        self.body.pack(padx=18, pady=18, fill='both', expand=True)

        # Top label
        tk.Label(self.body, text='в—€  N E Y R O N Y A  в—€',
                 bg='#1a1a1a', fg='#2a5a2a',
                 font=('Courier New', 9, 'bold')).pack(pady=(10, 2))

        # Decorative screws
        screw_row = tk.Frame(self.body, bg='#1a1a1a')
        screw_row.pack(fill='x', padx=10)
        tk.Label(screw_row, text='в—‰', bg='#1a1a1a', fg='#333',
                 font=('Courier New', 8)).pack(side='left')
        tk.Label(screw_row, text='в—‰', bg='#1a1a1a', fg='#333',
                 font=('Courier New', 8)).pack(side='right')

    def _build_screen(self):
        # Screen bezel
        bezel = tk.Frame(self.body, bg='#111', bd=0,
                         highlightbackground=BORDER, highlightthickness=2)
        bezel.pack(padx=14, pady=6, fill='both', expand=True)

        # Inner screen glow frame
        glow = tk.Frame(bezel, bg=SCREEN_BG, bd=0,
                        highlightbackground='#0a2a0a', highlightthickness=1)
        glow.pack(padx=3, pady=3, fill='both', expand=True)

        # Text widget вЂ” the CRT screen
        self.screen = tk.Text(
            glow,
            bg=SCREEN_BG, fg=GREEN,
            font=FONT_MONO,
            width=68, height=26,
            bd=0, relief='flat',
            insertbackground=GREEN,
            selectbackground='#003300',
            selectforeground=GREEN,
            wrap='none',
            state='disabled',
            cursor='none'
        )
        self.screen.pack(padx=6, pady=6)

        # Color tags
        self.screen.tag_config('title',  foreground=CYAN,      font=('Courier New', 11, 'bold'))
        self.screen.tag_config('sep',    foreground=GREEN_DIM)
        self.screen.tag_config('ok',     foreground='#00ff41')
        self.screen.tag_config('warn',   foreground=AMBER)
        self.screen.tag_config('err',    foreground=RED)
        self.screen.tag_config('dim',    foreground=GRAY)
        self.screen.tag_config('hi',     foreground=CYAN)
        self.screen.tag_config('prompt', foreground=AMBER,     font=('Courier New', 11, 'bold'))
        self.screen.tag_config('yes_sel',foreground=SCREEN_BG, background='#00ff41')
        self.screen.tag_config('no_sel', foreground=SCREEN_BG, background=RED)
        self.screen.tag_config('yes_dim',foreground='#00ff41')
        self.screen.tag_config('no_dim', foreground=RED)
        self.screen.tag_config('bar',    foreground=AMBER)

    def _build_buttons(self):
        btn_row = tk.Frame(self.body, bg='#1a1a1a')
        btn_row.pack(pady=(4, 12))

        style = dict(bg='#2a2a2a', fg='#555', relief='raised',
                     font=('Courier New', 8), bd=2, width=8,
                     activebackground='#3a3a3a', activeforeground=GREEN)

        tk.Button(btn_row, text='[ ESC ]', **style,
                  command=self.root.destroy).pack(side='left', padx=6)

        # D-pad hint
        tk.Label(btn_row, text='в†ђ в†’ : select    ENTER : confirm',
                 bg='#1a1a1a', fg='#2a4a2a',
                 font=('Courier New', 8)).pack(side='left', padx=10)

        tk.Button(btn_row, text='[ RST ]', **style,
                  command=self._start_scan).pack(side='left', padx=6)

        self.progress_var = tk.IntVar(value=0)
        self.progress_bar = ttk.Progressbar(self.body, variable=self.progress_var, maximum=100, mode='determinate')
        self.progress_bar.pack(fill='x', padx=18, pady=(0, 4))

        # Bottom brand
        tk.Label(self.body, text='RESTORE UNIT  v3.0',
                 bg='#1a1a1a', fg='#1a3a1a',
                 font=('Courier New', 7)).pack(pady=(0, 8))

    # в”Ђв”Ђ Screen output в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _write(self, text, tag=None):
        self.screen.config(state='normal')
        if tag:
            self.screen.insert('end', text, tag)
        else:
            self.screen.insert('end', text)
        self.screen.see('end')
        self.screen.config(state='disabled')
        self.root.update_idletasks()

    def _clear(self):
        self.screen.config(state='normal')
        self.screen.delete('1.0', 'end')
        self.screen.config(state='disabled')

    def _writeln(self, text='', tag=None):
        self._write(text + '\n', tag)

    def _render_fallout_progress(self, value):
        value = max(0, min(100, int(value)))
        self.progress_var.set(value)
        filled = value // 5
        bar = '[' + ('#' * filled).ljust(20, '-') + f'] {value:3}%'
        self._writeln('  VAULT-TEC RESTORE-O-METER ' + bar, 'bar')

    # в”Ђв”Ђ Scan phase в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _start_scan(self):
        self._clear()
        self.confirm_active = False
        self._writeln('=' * 60, 'sep')
        self._writeln('   NEYRONYA - Vosstanovlenie iz bekapa', 'title')
        self._writeln('=' * 60, 'sep')
        self._writeln()
        self._writeln('  >> Poisk arkhivov na diske B:\\...', 'warn')
        self._render_fallout_progress(5)
        self._writeln()

        self.backups = find_backups()

        if not self.backups:
            self._writeln("  !!  Arkhivy 'Vita V*.rar' ne naydeny v B:\\", 'err')
            return

        self._writeln('  Naydennye bekapi:', 'ok')
        self._writeln()
        for i, b in enumerate(self.backups):
            num    = f'[{i+1:2}]'
            name   = b['name'][:42].ljust(42)
            size   = b['size'].rjust(8)
            date   = b['mtime']
            marker = '  [noveyshiy]' if i == 0 else ''
            line   = f'  {num}  {name}  {size}   {date}{marker}\n'
            tag    = 'hi' if i == 0 else 'dim'
            self._write(line, tag)

        self._writeln()
        self._writeln('  [  0]  Vykhod', 'dim')
        self._writeln()
        self._writeln('  Vyberite bekap strelkami (Enter = vosstanovit):', 'prompt')
        self._writeln()

        self._enter_backup_select()

    # в”Ђв”Ђ Backup selection в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _enter_backup_select(self):
        self.mode = 'select'
        self.selected_idx = 0
        self._render_backup_select()

    def _render_backup_select(self):
        self.screen.config(state='normal')
        content = self.screen.get('1.0', 'end')
        marker = '\n  Vyberite bekap strelkami (Enter = vosstanovit):\n'
        if marker in content:
            cut = content.index(marker) + len(marker)
            self.screen.delete(f'1.0 + {cut} chars', 'end')

        self.screen.insert('end', '\n')
        for i, b in enumerate(self.backups):
            prefix = '  > ' if i == self.selected_idx else '    '
            name = b['name'][:42].ljust(42)
            line = f'{prefix}[{i+1:2}]  {name}  {b["size"].rjust(8)}   {b["mtime"]}\n'
            self.screen.insert('end', line, 'hi' if i == self.selected_idx else 'dim')
        self.screen.insert('end', '\n  [ в†‘ в†“ / в†ђ в†’ ] select    [ ENTER ] confirm    [ ESC ] exit\n', 'dim')
        self.screen.see('end')
        self.screen.config(state='disabled')
        self.root.update_idletasks()

    # в”Ђв”Ђ Key handler в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _on_key(self, event):
        if self.confirm_active:
            self._handle_confirm_key(event)
            return

        if not hasattr(self, 'mode'):
            return

        if self.mode == 'select':
            if event.keysym in ('Escape',):
                self.root.destroy()
            elif event.keysym in ('Up', 'Left', 'w', 'a'):
                self.selected_idx = (self.selected_idx - 1) % len(self.backups)
                self._render_backup_select()
            elif event.keysym in ('Down', 'Right', 's', 'd'):
                self.selected_idx = (self.selected_idx + 1) % len(self.backups)
                self._render_backup_select()
            elif event.keysym == 'Return':
                self._submit_selected_backup()

    def _submit_selected_backup(self):
        chosen = self.backups[self.selected_idx]
        self.chosen_backup = chosen

        self.screen.config(state='normal')
        content = self.screen.get('1.0', 'end')
        marker = '\n  Vyberite bekap strelkami (Enter = vosstanovit):\n'
        if marker in content:
            cut = content.index(marker)
            self.screen.delete(f'1.0 + {cut} chars', 'end')
        self.screen.config(state='disabled')

        self._writeln(f'\n  Vybran: {chosen["name"]}', 'hi')
        self._writeln()

        # Check extractor
        if os.path.exists(WINRAR):
            self._writeln(f'  OK  Raspakovshchik: {WINRAR}', 'ok')
        else:
            self._writeln('  !!  WinRAR ne nayden!', 'err')
            return

        self._writeln('  >> Proverka tselostnosti arkhiva...', 'warn')
        self.root.update_idletasks()

        ok = self._test_archive(chosen['path'])
        if not ok:
            self._writeln('  !!  Arkhiv povrezhden!', 'err')
            return
        self._writeln('  OK  Arkhiv v poryadke.', 'ok')
        self._render_fallout_progress(45)
        self._writeln()
        archive_has_myenv = self._archive_has_myenv()
        self._writeln(f'  Archive has myenv: {archive_has_myenv}', 'warn')
        if archive_has_myenv and is_running_from_project_myenv():
            self._writeln('  !!  Restore with myenv is blocked.', 'err')
            self._writeln('  !!  GUI zapushchen iz aktivnoy PROJECT_ROOT\\myenv.', 'err')
            self._writeln('  !!  ' + describe_busy_myenv_error(), 'err')
            self.mode = None
            return
        self._writeln(f'  VNIMANIE: Papka {PROJECT_ROOT} budet ochishchena posle temp-raspakovki!', 'err')
        if archive_has_myenv:
            self._writeln('  Tekushchaya myenv budet zamenena tolko posle uspeshnoy temp-raspakovki.', 'warn')
        else:
            self._writeln('  Tekushchaya myenv budet sokhranena (backup bez myenv).', 'ok')
        self._writeln('  Sokhranyatsya debug_menu, archive_gui.py, debug menu.bat, restore_gui.py, restore.bat, restore.ps1', 'warn')
        self._writeln()

        self._show_gta_confirm()

    # в”Ђв”Ђ Archive test в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _test_archive(self, path):
        try:
            r = subprocess.run([WINRAR, 't', path],
                               capture_output=True, timeout=60)
            return r.returncode == 0
        except Exception:
            return False

    # в”Ђв”Ђ GTA-style confirm в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _show_gta_confirm(self):
        self.confirm_active = True
        self.confirm_choice = 0  # 0=NO, 1=YES
        self._render_gta_confirm()

    def _render_gta_confirm(self):
        # Remove old confirm block
        self.screen.config(state='normal')
        content = self.screen.get('1.0', 'end')
        # Find marker and cut from there
        marker = '\n  Prodolzhit?\n'
        if marker in content:
            cut = content.index(marker)
            self.screen.delete(f'1.0 + {cut} chars', 'end')

        self.screen.insert('end', '\n  Prodolzhit?\n\n', 'prompt')

        # Render YES / NO boxes
        if self.confirm_choice == 1:
            self.screen.insert('end', '      ')
            self.screen.insert('end', '  YES  ', 'yes_sel')
            self.screen.insert('end', '        ')
            self.screen.insert('end', '  NO  ', 'no_dim')
        else:
            self.screen.insert('end', '      ')
            self.screen.insert('end', '  YES  ', 'yes_dim')
            self.screen.insert('end', '        ')
            self.screen.insert('end', '  NO  ', 'no_sel')

        self.screen.insert('end', '\n\n')
        self.screen.insert('end', '  [ <- -> ] select    [ ENTER ] confirm\n', 'dim')
        self.screen.see('end')
        self.screen.config(state='disabled')
        self.root.update_idletasks()

    def _handle_confirm_key(self, event):
        if event.keysym in ('Left', 'Right', 'a', 'd'):
            self.confirm_choice = 1 - self.confirm_choice
            self._render_gta_confirm()
        elif event.keysym == 'Return':
            self.confirm_active = False
            if self.confirm_choice == 1:
                self._do_restore()
            else:
                self._writeln('\n\n  Otmeneno.', 'dim')
                self.mode = None

    # в”Ђв”Ђ Restore в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    def _archive_has_myenv(self):
        name = os.path.splitext(self.chosen_backup['name'])[0]
        parts = name.upper().split()
        return 'UN' not in parts and 'UM' not in parts

    def _clean_project(self, replace_myenv):
        if replace_myenv and is_running_from_project_myenv():
            raise RuntimeError(describe_busy_myenv_error())
        for item in os.listdir(PROJECT_ROOT):
            if item in KEEP_FILES:
                continue
            if item.lower() == 'myenv' and not replace_myenv:
                self._writeln('  KEEP myenv (backup bez myenv)', 'ok')
                continue
            full = os.path.join(PROJECT_ROOT, item)
            try:
                if os.path.isdir(full):
                    import shutil
                    shutil.rmtree(full)
                else:
                    os.remove(full)
            except (PermissionError, OSError) as e:
                self._writeln(f'  !!  Ne udalos udalit: {item}: {e}', 'err')
                if item.lower() == 'myenv' and replace_myenv:
                    raise RuntimeError(describe_busy_myenv_error()) from e
            except Exception as e:
                self._writeln(f'  !!  Ne udalos udalit: {item}: {e}', 'err')

    def _do_restore(self):
        # Extract to temp on B: drive before deleting anything from the project.
        import tempfile, shutil
        os.makedirs(TEMP_ROOT, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix='neyronya_restore_', dir=TEMP_ROOT)
        self._writeln('\n\n  >> Raspakovka vo vremennuyu papku...', 'warn')
        self.root.update_idletasks()

        def run_extract():
            try:
                r = subprocess.run(
                    [WINRAR, 'x', self.chosen_backup['path'], tmp + '\\', '-y'],
                    capture_output=True, timeout=300
                )
                self.root.after(0, lambda: self._after_extract(tmp, r.returncode))
            except Exception as e:
                self.root.after(0, lambda: self._writeln(f'  !!  Oshibka: {e}', 'err'))

        threading.Thread(target=run_extract, daemon=True).start()

    def _after_extract(self, tmp, code):
        if code != 0:
            self._writeln(f'  !!  Oshibka raspakovki (kod {code})', 'err')
            return
        self._writeln('  OK  Raspakovka zavershena.', 'ok')
        self._render_fallout_progress(75)

        import shutil
        items = os.listdir(tmp)
        src = tmp
        if len(items) == 1 and os.path.isdir(os.path.join(tmp, items[0])):
            src = os.path.join(tmp, items[0])

        replace_myenv = self._archive_has_myenv()
        self._writeln('  >> Ochistka papki proekta...', 'warn')
        self.root.update_idletasks()
        try:
            self._clean_project(replace_myenv)
        except RuntimeError as e:
            self._writeln(f'  !!  {e}', 'err')
            shutil.rmtree(tmp, ignore_errors=True)
            self.mode = None
            return
        self._writeln('  OK  Papka ochishchena.', 'ok')
        self._render_fallout_progress(90)

        self._writeln('  >> Kopirovanie faylov...', 'warn')
        self.root.update_idletasks()

        for item in os.listdir(src):
            s = os.path.join(src, item)
            d = os.path.join(PROJECT_ROOT, item)
            try:
                if os.path.isdir(s):
                    if os.path.exists(d):
                        shutil.rmtree(d)
                    shutil.copytree(s, d)
                else:
                    shutil.copy2(s, d)
            except Exception as e:
                self._writeln(f'  !!  {item}: {e}', 'err')

        shutil.rmtree(tmp, ignore_errors=True)

        self._writeln('  OK  Fayly skopirovany.', 'ok')
        self._render_fallout_progress(100)
        self._writeln()
        self._writeln('=' * 60, 'sep')
        self._writeln('   Vosstanovlenie zaversheno uspeshno!', 'title')
        self._writeln(f'   Bekap: {self.chosen_backup["name"]}', 'ok')
        self._writeln('=' * 60, 'sep')
        self.mode = None

# в”Ђв”Ђ Entry point в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def log_startup_error(exc):
    log_dir = os.path.join(PROJECT_ROOT, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, 'restore_gui_error.log')
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write('\n' + '=' * 80 + '\n')
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
            f.write('Executable: ' + sys.executable + '\n')
            f.write('CWD: ' + os.getcwd() + '\n')
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
        return log_path
    except Exception:
        fallback = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'restore_gui_error.log')
        with open(fallback, 'a', encoding='utf-8') as f:
            f.write('\n' + '=' * 80 + '\n')
            f.write(time.strftime('%Y-%m-%d %H:%M:%S') + '\n')
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=f)
        return fallback

def show_startup_error(exc, log_path):
    message = f'РќРµ СѓРґР°Р»РѕСЃСЊ РѕС‚РєСЂС‹С‚СЊ РјРµРЅСЋ РІРѕСЃСЃС‚Р°РЅРѕРІР»РµРЅРёСЏ.\n\nРћС€РёР±РєР°: {exc}\n\nРџРѕРґСЂРѕР±РЅРѕСЃС‚Рё Р·Р°РїРёСЃР°РЅС‹ РІ:\n{log_path}'
    try:
        messagebox.showerror('NEYRONYA RESTORE - РѕС€РёР±РєР° Р·Р°РїСѓСЃРєР°', message)
    except Exception:
        print(message, file=sys.stderr)
        input('РќР°Р¶РјРёС‚Рµ Enter РґР»СЏ РІС‹С…РѕРґР°...')

def force_window_to_front(root, release_after_ms=2500):
    """Force the Tk window to the foreground on Windows, then release topmost."""
    try:
        root.deiconify()
        root.lift()
        root.focus_force()
        root.attributes('-topmost', True)
        root.update_idletasks()
    except tk.TclError:
        return

    def release_topmost():
        try:
            root.attributes('-topmost', False)
        except tk.TclError:
            pass

    root.after(release_after_ms, release_topmost)

def main():
    root = tk.Tk()
    root.configure(bg='#0a0a0a')

    # Center window
    w, h = 720, 640
    root.geometry(f'{w}x{h}')
    root.update_idletasks()
    sw = root.winfo_screenwidth()
    sh = root.winfo_screenheight()
    root.geometry(f'{w}x{h}+{(sw-w)//2}+{(sh-h)//2}')

    app = RestoreApp(root)

    force_window_to_front(root)
    root.after(150, lambda: force_window_to_front(root, release_after_ms=2350))

    # Safe startup check for diagnostics: creates the window, runs pending
    # Tk events, then exits without entering the GUI loop or restoring files.
    if os.environ.get('NEYRONYA_RESTORE_TEST') == '1':
        root.update()
        print('RESTORE_GUI_START_OK', flush=True)
        root.destroy()
        return

    root.mainloop()

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log_path = log_startup_error(exc)
        show_startup_error(exc, log_path)
        raise

