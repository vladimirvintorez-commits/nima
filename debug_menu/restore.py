import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from . import snapshots
from .archive import find_rar_exe, _NO_WINDOW
from .config import DEBUG_FILES, INC_MANIFEST_NAME, PROJECT_ROOT, version_from_name


def is_debug_menu_item(path: Path) -> bool:
    """True для launch/debug-menu файлов и папок, которые нельзя удалять при restore."""
    try:
        rel = path.relative_to(PROJECT_ROOT)
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] in DEBUG_FILES


def resolve_restore_source(temp_dir: Path) -> Path:
    """
    Возвращает папку, содержимое которой нужно копировать в PROJECT_ROOT.

    RAR-архив создаётся с корневой папкой проекта (например, Neyronya),
    поэтому после извлечения во временной папке часто появляется один
    верхнеуровневый каталог. В этом случае восстанавливаем содержимое этого
    каталога, а не сам каталог, чтобы не получить PROJECT_ROOT/Neyronya/...
    Если же архив уже содержит файлы прямо в корне, копируем из temp_dir.
    """
    top_level_items = [item for item in temp_dir.iterdir()]
    dirs = [item for item in top_level_items if item.is_dir()]
    files = [item for item in top_level_items if item.is_file()]

    if len(dirs) == 1 and not files:
        return dirs[0]

    project_named_dir = temp_dir / PROJECT_ROOT.name
    if project_named_dir.is_dir() and not any(item.name != PROJECT_ROOT.name for item in top_level_items):
        return project_named_dir

    return temp_dir


def _extract_archive(archive_path, temp_dir, progress_callback):
    """Проверяет целостность и извлекает RAR-архив во временную папку."""
    rar_exe = find_rar_exe()
    if not rar_exe:
        raise RuntimeError("rar.exe/WinRAR.exe не найден.")

    progress_callback("Проверка целостности архива...", 5, "determinate")
    test_result = subprocess.run([rar_exe, "t", str(archive_path)], capture_output=True, text=True,
                                 encoding="cp866", errors="replace", creationflags=_NO_WINDOW)
    if test_result.returncode != 0:
        raise RuntimeError(f"Архив повреждён:\n{test_result.stderr or test_result.stdout}")

    progress_callback("Извлечение архива во временную папку...", 15, "determinate")
    extract_result = subprocess.run(
        [rar_exe, "x", "-y", str(archive_path), str(temp_dir) + "\\"],
        creationflags=_NO_WINDOW,
        capture_output=True,
        text=True,
        encoding="cp866",
        errors="replace",
    )
    if extract_result.returncode != 0:
        raise RuntimeError(f"Ошибка извлечения:\n{extract_result.stderr or extract_result.stdout}")


def _remove_path(path: Path):
    """Удаляет файл или папку. Возвращает True, если что-то было удалено."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
            return True
        if path.exists():
            path.unlink()
            return True
    except Exception:
        return False
    return False


def _prune_empty_dirs(touched_paths):
    """Убирает опустевшие после удаления файлов папки (например, добавленные
    папки, в которых лежали только файлы из сохранения)."""
    seen = set()
    for path in touched_paths:
        folder = path.parent
        while folder != PROJECT_ROOT and folder not in seen:
            seen.add(folder)
            try:
                folder.rmdir()  # удаляется только если папка пуста
            except OSError:
                break
            folder = folder.parent


def read_incremental_manifest(archive_path: Path):
    """Манифест частичного сохранения без распаковки всего архива.

    Возвращает dict манифеста или None, если архив не частичный.
    """
    rar_exe = find_rar_exe()
    if not rar_exe:
        raise RuntimeError("rar.exe/WinRAR.exe не найден.")

    listing = subprocess.run([rar_exe, "lb", str(archive_path)], capture_output=True, text=True,
                             encoding="cp866", errors="replace", creationflags=_NO_WINDOW)
    if listing.returncode != 0:
        raise RuntimeError(f"Архив повреждён:\n{listing.stderr or listing.stdout}")
    manifest_arcname = next(
        (line.strip() for line in listing.stdout.splitlines() if line.strip().endswith(INC_MANIFEST_NAME)),
        None)
    if not manifest_arcname:
        return None

    temp_dir = Path(tempfile.gettempdir()) / f"vita_manifest_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        extract_result = subprocess.run(
            [rar_exe, "x", "-y", str(archive_path), manifest_arcname, str(temp_dir) + "\\"],
            capture_output=True, text=True, encoding="cp866", errors="replace",
            creationflags=_NO_WINDOW)
        if extract_result.returncode != 0:
            raise RuntimeError(f"Ошибка извлечения манифеста:\n{extract_result.stderr or extract_result.stdout}")
        return json.loads((temp_dir / manifest_arcname).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def restore_archive(archive_path: Path, progress_callback):
    """
    Восстанавливает проект из полного RAR-архива.
    1. Извлекает во временную папку
    2. Определяет фактический корень восстановленных файлов
    3. Удаляет содержимое PROJECT_ROOT (кроме DEBUG_FILES/debug_menu)
    4. Копирует содержимое найденного корня в PROJECT_ROOT, не перезаписывая DEBUG_FILES/debug_menu
    5. Обновляет снимок проекта (базу частичных сохранений)
    6. Удаляет временную папку
    """
    archive_name = archive_path.name
    if " INC " in archive_name:
        raise RuntimeError("Это частичное сохранение: для него используйте точечное восстановление.")
    has_myenv = ("UN" not in archive_name.upper() and "UM" not in archive_name.upper())

    temp_dir = Path(tempfile.gettempdir()) / f"vita_restore_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        _extract_archive(archive_path, temp_dir, progress_callback)
        extracted_root = resolve_restore_source(temp_dir)

        progress_callback("Очистка текущего проекта...", 50, "determinate")
        for item in PROJECT_ROOT.iterdir():
            if is_debug_menu_item(item):
                continue
            if item.name == "myenv" and not has_myenv:
                continue
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            except Exception as e:
                progress_callback(f"Пропуск {item.name}: {e}", None, None)

        progress_callback("Копирование файлов...", 70, "determinate")
        for item in extracted_root.iterdir():
            dest = PROJECT_ROOT / item.name
            if is_debug_menu_item(dest):
                continue
            try:
                if item.is_dir():
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)
            except Exception as e:
                progress_callback(f"Ошибка копирования {item.name}: {e}", None, None)

        progress_callback("Обновление снимка проекта...", 92, "indeterminate")
        snapshots.record_save("restore", archive_name, version_from_name(archive_name) or "?",
                              snapshots.scan_snapshot())
        progress_callback("Восстановление завершено!", 100, "determinate")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def restore_incremental_archive(archive_path: Path, progress_callback):
    """Точечное восстановление частичного (инкрементального) сохранения.

    Проект целиком не трогается. По манифесту архива:
    - файлы из списка changed удаляются в текущем проекте и заменяются
      версиями из сохранения (откат изменений);
    - пути из списка deleted (на момент сохранения их не существовало)
      удаляются из проекта, если появились после сохранения;
    - опустевшие папки убираются.
    После отката снимок проекта становится новой базой частичных сохранений.
    """
    archive_name = archive_path.name
    temp_dir = Path(tempfile.gettempdir()) / f"vita_restore_{uuid.uuid4().hex[:8]}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        _extract_archive(archive_path, temp_dir, progress_callback)
        extracted_root = resolve_restore_source(temp_dir)

        manifest_path = extracted_root / INC_MANIFEST_NAME
        if not manifest_path.exists():
            raise RuntimeError("В архиве нет манифеста частичного сохранения — восстановление невозможно.")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise RuntimeError(f"Манифест частичного сохранения повреждён: {e}")

        changed = [snapshots.validate_rel_path(p) for p in manifest.get("changed", [])]
        deleted = [snapshots.validate_rel_path(p) for p in manifest.get("deleted", [])]
        version = str(manifest.get("version") or version_from_name(archive_name) or "?")

        progress_callback("Откат изменённых файлов...", 50, "determinate")
        restored, touched = 0, []
        for rel in changed:
            if rel.split("/", 1)[0].lower() == "debug_menu":
                continue
            src = extracted_root / rel
            dest = PROJECT_ROOT / rel
            if not src.is_file():
                progress_callback(f"Пропуск (нет в архиве): {rel}", None, None)
                continue
            _remove_path(dest)
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
            except Exception as e:
                progress_callback(f"Ошибка отката {rel}: {e}", None, None)
                continue
            touched.append(dest)
            restored += 1

        progress_callback("Удаление файлов, появившихся после сохранения...", 70, "determinate")
        removed = 0
        for rel in deleted:
            if rel.split("/", 1)[0].lower() == "debug_menu":
                continue
            dest = PROJECT_ROOT / rel
            if _remove_path(dest):
                touched.append(dest)
                removed += 1
            else:
                progress_callback(f"Не удалось удалить: {rel}", None, None)
        _prune_empty_dirs(touched)

        progress_callback("Обновление снимка проекта...", 92, "indeterminate")
        snapshots.record_save("restore", archive_name, version, snapshots.scan_snapshot(),
                              extra={"changed": restored, "deleted": removed})
        progress_callback("Восстановление завершено!", 100, "determinate")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
