"""Снимки состояния проекта для частичных (инкрементальных) сохранений.

При каждом сохранении (полном или частичном) дебаг-меню фиксирует снимок
проекта — {относительный posix-путь: [mtime, size]} — и запоминает его в
реестре SNAPSHOTS_PATH вместе с версией сохранения. Частичное сохранение
считает разницу между последним снимком (базой) и текущим состоянием,
точечное восстановление откатывает только файлы из архива.

Зависит только от config: archive.py и restore.py импортируют этот модуль,
сам он их не импортирует.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from .config import EXCLUDED_ALWAYS, EXCLUDED_COMMON, PROJECT_ROOT, SNAPSHOTS_PATH, TEMP_SUFFIXES

SAVE_HISTORY_LIMIT = 100


def _is_tracked(rel: Path) -> bool:
    """Фильтр отслеживаемых путей: как полный архив без myenv, плюс сама
    папка debug_menu не отслеживается — она защищена при restore и её файлы
    никогда не попадают в частичный архив."""
    parts_lower = {part.lower() for part in rel.parts}
    if not parts_lower:
        return True
    if parts_lower & EXCLUDED_ALWAYS or parts_lower & EXCLUDED_COMMON:
        return False
    if "myenv" in parts_lower:
        return False
    if rel.parts[0].lower() == "debug_menu":
        return False
    if rel.suffix.lower() in TEMP_SUFFIXES:
        return False
    return True


def scan_snapshot(root=None):
    """Снимок проекта: {относительный posix-путь: [mtime, size]}.

    myenv, __pycache__, временные файлы и debug_menu не отслеживаются, то есть
    частичное сохранение — это дифф к «полному архиву без myenv».
    """
    base = Path(root) if root is not None else PROJECT_ROOT
    snapshot = {}
    for cur_dir, dirs, files in os.walk(base):
        rel_dir = Path(cur_dir).relative_to(base)
        if rel_dir.parts and not _is_tracked(rel_dir):
            dirs[:] = []
            continue
        # Служебные папки отсекаются до спуска в них (myenv большой).
        dirs[:] = [d for d in dirs if _is_tracked(rel_dir / d)]
        for name in files:
            rel = rel_dir / name
            if not _is_tracked(rel):
                continue
            try:
                st = (base / rel).stat()
            except OSError:
                continue
            snapshot[rel.as_posix()] = [round(st.st_mtime, 3), st.st_size]
    return snapshot


def diff_snapshots(base, current):
    """Разница двух снимков: (изменённые/новые пути, исчезнувшие пути)."""
    changed = sorted(rel for rel, meta in current.items() if base.get(rel) != meta)
    deleted = sorted(rel for rel in base if rel not in current)
    return changed, deleted


def load_registry(path=None):
    registry_path = path if path is not None else SNAPSHOTS_PATH
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except Exception:
        return {"saves": [], "base": None}
    if not isinstance(data, dict):
        return {"saves": [], "base": None}
    if not isinstance(data.get("saves"), list):
        data["saves"] = []
    if not isinstance(data.get("base"), dict):
        data["base"] = None
    return data


def save_registry(registry, path=None):
    registry_path = path if path is not None else SNAPSHOTS_PATH
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = registry_path.with_name(registry_path.name + ".tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(registry_path)


def has_base(path=None):
    base = load_registry(path).get("base")
    return bool(base and base.get("snapshot"))


def record_save(save_type, archive_name, version, snapshot, extra=None, path=None):
    """Фиксирует сохранение: запись в историю + снимок как новая база сравнения.

    save_type: full | incremental | restore. Возвращает созданную запись.
    """
    registry = load_registry(path)
    entry = {
        "type": save_type,
        "archive": archive_name,
        "version": version,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "files": len(snapshot),
    }
    if extra:
        entry.update(extra)
    registry["saves"].append(entry)
    del registry["saves"][:-SAVE_HISTORY_LIMIT]
    registry["base"] = {"save": entry, "snapshot": snapshot}
    save_registry(registry, path)
    return entry


def validate_rel_path(value):
    """Нормализует путь из манифеста (в posix) и отклоняет выход за пределы
    проекта: абсолютные пути и переходы «..» запрещены."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Некорректный путь в манифесте: {value!r}")
    text = value.strip().replace("\\", "/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or ".." in parts or text[1:2] == ":" or text.startswith("//"):
        raise ValueError(f"Путь вне проекта в манифесте: {value!r}")
    return "/".join(parts)
