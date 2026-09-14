import importlib
import subprocess
import sys
import traceback

from .config import PROJECT_MODULES, PROJECT_ROOT

# Скрытие консольного окна дочернего процесса на Windows (no-op на других ОС).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _venv_python():
    """Путь к python из myenv, если он есть (тяжёлые пакеты ставятся только туда)."""
    candidate = PROJECT_ROOT / "myenv" / "Scripts" / "python.exe"
    return str(candidate) if candidate.is_file() else None


def _import_via_venv(module_name: str, attr_name: str) -> tuple[str, str]:
    """Импортирует модуль в интерпретаторе myenv (torch/speech_recognition/audioop
    отсутствуют в системном Python и несовместимы между версиями)."""
    venv = _venv_python()
    if not venv:
        return "FAIL", "myenv/Scripts/python.exe не найден"
    code = f"import {module_name}; getattr({module_name}, {attr_name!r}, None)"
    try:
        proc = subprocess.run(
            [venv, "-c", code],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=120, creationflags=_NO_WINDOW,
        )
        if proc.returncode == 0:
            return "OK", ""
        return "FAIL", (proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else f"exit={proc.returncode}"
    except Exception as e:
        return "FAIL", str(e)


def test_all_modules():
    """
    Пытается импортировать каждый модуль проекта.
    Сначала обычным импортом; если не вышло — в интерпретаторе myenv.
    Возвращает список (module_name, status, error_text).
    """
    results = []
    orig_path = sys.path.copy()
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    for module_name, attr_name in PROJECT_MODULES:
        try:
            mod = importlib.import_module(module_name)
            if attr_name and not hasattr(mod, attr_name):
                results.append((module_name, "WARN", f"Нет атрибута '{attr_name}'"))
                continue
            results.append((module_name, "OK", ""))
        except ImportError as e:
            # Тяжёлые зависимости живут в myenv (Python 3.12), а меню может быть
            # запущено системным Python другой версии — импортируем в venv.
            status, detail = _import_via_venv(module_name, attr_name)
            if status == "OK":
                results.append((module_name, "OK", "(импортировано через myenv)"))
            else:
                results.append((module_name, "FAIL", f"{e} | venv: {detail}"))
        except Exception as e:
            traceback.format_exc()
            results.append((module_name, "FAIL", str(e)))

    sys.path = orig_path
    return results
