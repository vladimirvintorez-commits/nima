@echo off
cd /d "%~dp0"
REM v12: myenv удалён, меню живёт на системном Python 3.12 (tkinter есть).
REM Лаунчер перенесён в debug_menu/archive_gui.py. start "" — чтобы .bat не ждал.
set PYW=C:\Users\Vladi\AppData\Local\Programs\Python\Python312\pythonw.exe
if exist "%PYW%" (
    start "" "%PYW%" "%~dp0debug_menu\archive_gui.py"
) else (
    start "" pythonw "%~dp0debug_menu\archive_gui.py"
)
