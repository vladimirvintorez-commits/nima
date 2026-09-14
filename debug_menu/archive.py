import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from . import snapshots
from .config import (BACKUP_DESTINATION, EXCLUDED_ALWAYS, EXCLUDED_COMMON, INC_MANIFEST_NAME,
                     INC_TAG, PROJECT_ROOT, TEMP_SUFFIXES, get_version)

# Скрытие консольного окна дочернего процесса на Windows. На других ОС флаг
# отсутствует и getattr вернёт 0 (no-op), поэтому вызовы остаются кроссплатформенными.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def sanitize_description(value):
    words = re.findall(r"[\wА-Яа-яЁё-]+", value.strip(), flags=re.UNICODE)[:2]
    result = " ".join(words) if words else "backup"
    return re.sub(r'[<>:"/\\|?*]', "_", result)


def find_rar_exe():
    for name in ("rar.exe", "WinRAR.exe"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        Path(r"B:\\Winrar") / "rar.exe",
        Path(r"B:\\Winrar") / "WinRAR.exe",
        Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "WinRAR" / "rar.exe",
        Path(os.environ.get("ProgramFiles", r"C:\\Program Files")) / "WinRAR" / "WinRAR.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "WinRAR" / "rar.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\\Program Files (x86)")) / "WinRAR" / "WinRAR.exe",
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def find_backups():
    """Возвращает список .rar бэкапов из B:\\, отсортированных по дате (старые сначала).

    Ищем и новые архивы «Нимфея V*», и старые «Vita V*», чтобы история бэкапов
    не пропадала после переименования персонажа.
    """
    backups = []
    seen = set()
    try:
        for pattern in ("Нимфея V*.rar", "Vita V*.rar"):
            for f in BACKUP_DESTINATION.glob(pattern):
                if f not in seen:
                    seen.add(f)
                    backups.append(f)
    except Exception:
        pass
    backups.sort(key=lambda p: p.stat().st_mtime)
    return backups


def should_exclude(path, exclude_myenv, archive_path):
    if path == archive_path:
        return True
    rel = path.relative_to(PROJECT_ROOT)
    parts_lower = {part.lower() for part in rel.parts}
    if parts_lower & EXCLUDED_ALWAYS:
        return True
    if exclude_myenv and "myenv" in parts_lower:
        return True
    if parts_lower & EXCLUDED_COMMON:
        return True
    if path.is_file() and path.suffix.lower() in TEMP_SUFFIXES:
        return True
    return False


def iter_files(exclude_myenv, archive_path):
    for root, dirs, files in os.walk(PROJECT_ROOT):
        root_path = Path(root)
        dirs[:] = [d for d in dirs if not should_exclude(root_path / d, exclude_myenv, archive_path)]
        for file_name in files:
            file_path = root_path / file_name
            if not should_exclude(file_path, exclude_myenv, archive_path):
                yield file_path


def create_rar(rar_exe, archive_path, exclude_myenv, progress_callback):
    progress_callback("Подготовка RAR-архива...", 10, "determinate")
    exclude_masks = [
        r"Neyronya\.postman", r"Neyronya\.postman\*",
        r"Neyronya\postman", r"Neyronya\postman\*",
        r"Neyronya\__pycache__", r"Neyronya\__pycache__\*",
        r"Neyronya\*\__pycache__", r"Neyronya\*\__pycache__\*",
        "*.pyc", "*.tmp",
        # Реестр снимков — служебный файл дебаг-меню, в бэкапы проекта не нужен.
        r"Neyronya\debug_menu\snapshots.json",
    ]
    if exclude_myenv:
        exclude_masks.extend([r"Neyronya\myenv", r"Neyronya\myenv\*"])

    cmd = [rar_exe, "a", "-r", str(archive_path)]
    cmd.extend(f"-x{mask}" for mask in exclude_masks)
    cmd.append(PROJECT_ROOT.name)

    progress_callback("Архивация выполняется...", 0, "indeterminate")
    # FIX: rar.exe печатает прогресс в OEM-кодировке (cp866); text=True без
    # errors="replace" падал UnicodeDecodeError в КОНЦЕ долгой архивации с myenv.
    result = subprocess.run(cmd, cwd=str(BACKUP_DESTINATION), capture_output=True, text=True,
                            encoding="cp866", errors="replace", creationflags=_NO_WINDOW)
    progress_callback("Проверка результата...", 90, "determinate")
    # rar: 0 — успех, 1 — предупреждение (архив создан; частая причина — длинные
    # пути внутри myenv). Ошибкой считаем только 2+ и отсутствие файла архива.
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr or result.stdout or "rar.exe завершился с ошибкой")
    if not archive_path.exists():
        raise RuntimeError("RAR-архив не был создан")
    progress_callback(f"Готово: {archive_path}", 100, "determinate")


def make_archive(include_myenv, description, progress_callback):
    version = get_version()
    description = sanitize_description(description)
    exclude_myenv = not include_myenv
    archive_tag = " UM" if exclude_myenv else ""
    base_name = f"Нимфея V{version}{archive_tag} {description}"

    progress_callback("Поиск rar.exe/WinRAR.exe...", 2, "determinate")
    rar_exe = find_rar_exe()
    if not rar_exe:
        raise RuntimeError("rar.exe/WinRAR.exe не найден. Архив не создан.")

    # Снимок берётся ДО архивации: база частичных сохранений обязана быть
    # подмножеством архива. Файлы, появившиеся во время архивации, в снимок
    # не попадут и будут сохранены следующим частичным сохранением.
    progress_callback("Сканирование файлов проекта...", 5, "indeterminate")
    state = snapshots.scan_snapshot()

    archive_path = BACKUP_DESTINATION / f"{base_name}.rar"
    create_rar(rar_exe, archive_path, exclude_myenv, progress_callback)
    snapshots.record_save("full", archive_path.name, version, state, extra={"myenv": include_myenv})
    return archive_path


def make_incremental_archive(description, progress_callback):
    """Частичное (инкрементальное) сохранение: RAR только с файлами, изменёнными
    или добавленными с момента последнего сохранения, плюс манифест со списком
    сохранённых и удалённых путей. Имя помечено тегом INC — не полный проект.

    Возвращает (путь к архиву, изменено, удалено).
    """
    progress_callback("Чтение снимка последнего сохранения...", 2, "determinate")
    base = snapshots.load_registry().get("base") or {}
    base_snapshot = base.get("snapshot")
    if not base_snapshot:
        raise RuntimeError("Нет базы для сравнения: сначала сделайте полное сохранение "
                           "(+myenv / -myenv) — оно зафиксирует снимок проекта.")

    progress_callback("Сканирование файлов проекта...", 5, "indeterminate")
    current = snapshots.scan_snapshot()
    changed, deleted = snapshots.diff_snapshots(base_snapshot, current)
    if not changed and not deleted:
        raise RuntimeError("С последнего сохранения изменений нет — частичный архив не нужен.")

    progress_callback("Поиск rar.exe/WinRAR.exe...", 8, "determinate")
    rar_exe = find_rar_exe()
    if not rar_exe:
        raise RuntimeError("rar.exe/WinRAR.exe не найден. Архив не создан.")

    version = get_version()
    description = sanitize_description(description)
    base_name = f"Нимфея V{version}{INC_TAG} {description}"
    archive_path = BACKUP_DESTINATION / f"{base_name}.rar"
    if archive_path.exists():
        archive_path = BACKUP_DESTINATION / f"{base_name} {datetime.now().strftime('%H-%M-%S')}.rar"

    progress_callback("Копирование изменённых файлов...", 15, "determinate")
    staging = Path(tempfile.mkdtemp(prefix="vita_inc_"))
    try:
        stage_root = staging / PROJECT_ROOT.name
        copied = []
        for index, rel in enumerate(changed):
            try:
                dest = stage_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(PROJECT_ROOT / rel, dest)
            except OSError:
                continue  # файл исчез во время сохранения — попадёт в следующий архив
            copied.append(rel)
            if index % 50 == 0:
                progress_callback(f"Копирование изменённых файлов ({index + 1}/{len(changed)})...", None, None)
        if not copied and not deleted:
            raise RuntimeError("Не удалось скопировать изменённые файлы — архив не создан.")

        base_save = base.get("save") or {}
        manifest = {
            "type": "incremental",
            "note": "Частичное сохранение — НЕ полный проект. Восстановление заменяет "
                    "только перечисленные файлы.",
            "version": version,
            "base_version": base_save.get("version"),
            "base_archive": base_save.get("archive"),
            "created": datetime.now().isoformat(timespec="seconds"),
            "changed": copied,
            "deleted": deleted,
        }
        (stage_root / INC_MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

        progress_callback("Создание RAR-архива...", 40, "indeterminate")
        result = subprocess.run([rar_exe, "a", "-r", str(archive_path), PROJECT_ROOT.name],
                                cwd=str(staging), capture_output=True, text=True,
                                encoding="cp866", errors="replace", creationflags=_NO_WINDOW)
        progress_callback("Проверка результата...", 90, "determinate")
        if result.returncode not in (0, 1):
            raise RuntimeError(result.stderr or result.stdout or "rar.exe завершился с ошибкой")
        if not archive_path.exists():
            raise RuntimeError("RAR-архив не был создан")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    # База = снимок, по которому считался diff. Исчезнувшие при копировании
    # файлы из базы убираются, чтобы следующее сохранение их не потеряло;
    # всё, что изменилось уже после сканирования, останется «изменённым».
    copied_set = set(copied)
    for rel in changed:
        if rel not in copied_set:
            current.pop(rel, None)
    snapshots.record_save("incremental", archive_path.name, version, current,
                          extra={"changed": len(copied), "deleted": len(deleted)})
    progress_callback(f"Готово: {archive_path.name} (изменено: {len(copied)}, удалено: {len(deleted)})",
                      100, "determinate")
    return archive_path, len(copied), len(deleted)
