"""Вкладка «Баги» в debug menu: ручная фиксация замеченных багов в docs/BUGS.md.

Каждая запись автоматически помечается:
- версией, на которой найден баг (текущая из CHANGELOG.md);
- файлами, менявшимися в этой версии (backtick-упоминания из её раздела);
- датой/временем.
Это ускоряет поиск виновника: сразу видно, что менялось непосредственно перед багом.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .config import CHANGELOG_PATH, PROJECT_ROOT

BUGS_PATH = PROJECT_ROOT / "docs" / "BUGS.md"

# Важность (насколько критичен баг) и срочность (решать сейчас или в копилку).
# Значения попадают в запись BUGS.md — по ним агент сортирует порядок работ.
IMPORTANCE_LEVELS = (
    ("critical", "🔴 Критичный — блокирует использование"),
    ("major", "🟠 Важный — заметно мешает"),
    ("minor", "🟢 Мелкий — терпимо / под вопросом"),
)
URGENCY_LEVELS = (
    ("now", "⚡ Решить сейчас же"),
    ("later", "📦 В копилку — можно отложить"),
)
DEFAULT_IMPORTANCE = "major"
DEFAULT_URGENCY = "now"

IMPORTANCE_LABELS = {k: label.split(" ", 1)[1] for k, label in IMPORTANCE_LEVELS}
URGENCY_LABELS = {k: label.split(" ", 1)[1] for k, label in URGENCY_LEVELS}

HEADER = """# BUGS.md — замеченные баги (заполняется из debug menu)

> Каждая запись: версия, на которой замечен баг, файлы этой версии, дата и описание.

"""


def _read_changelog() -> str:
    try:
        return CHANGELOG_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def get_current_version() -> str:
    text = _read_changelog()
    match = re.search(r"##\s+Vita\s+v?(\d+\.\d+\.\d+)", text)
    return match.group(1) if match else "?.?.?"


def get_version_section(version: str) -> str:
    """Текст раздела текущей версии из CHANGELOG (до следующего заголовка версии)."""
    text = _read_changelog()
    pattern = re.compile(rf"##\s+Vita\s+v{re.escape(version)}\b.*?(?=\n##\s+Vita\s+v\d+\.|\Z)", re.DOTALL)
    match = pattern.search(text)
    return match.group(0) if match else ""


def get_version_changed_files(version: str) -> list[str]:
    """Уникальные backtick-упоминания файлов/папок из раздела версии."""
    section = get_version_section(version)
    tokens = re.findall(r"`([^`\n]{2,60})`", section)
    seen, files = set(), []
    for token in tokens:
        token = token.strip()
        # Оставляем только похожее на пути/файлы: есть точка, слэш или известное имя.
        if not token or token in seen:
            continue
        if re.search(r"[\\/]|\.py$|\.js$|\.json$|\.md$|\.bat$|\.cjs$|\.mjs$|\.txt$", token):
            seen.add(token)
            files.append(token)
    return files[:15]


def load_bugs_text() -> str:
    try:
        return BUGS_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def append_bug(description: str, importance: str = DEFAULT_IMPORTANCE, urgency: str = DEFAULT_URGENCY) -> Path:
    description = " ".join(str(description or "").split()).strip()
    if not description:
        raise ValueError("Пустое описание бага")
    version = get_current_version()
    files = get_version_changed_files(version)
    stamp = datetime.now().strftime("%d.%m.%Y %H:%M")
    files_line = ", ".join(f"`{f}`" for f in files) if files else "—"
    imp = IMPORTANCE_LABELS.get(importance, IMPORTANCE_LABELS[DEFAULT_IMPORTANCE])
    urg = URGENCY_LABELS.get(urgency, URGENCY_LABELS[DEFAULT_URGENCY])
    entry = (
        f"\n## Баг от {stamp} — замечен на Vita v{version}\n\n"
        f"- **Описание:** {description}\n"
        f"- **Важность:** {imp}\n"
        f"- **Срочность:** {urg}\n"
        f"- **Файлы, менявшиеся в v{version}:** {files_line}\n\n"
    )
    if not BUGS_PATH.exists():
        BUGS_PATH.write_text(HEADER, encoding="utf-8")
    with open(BUGS_PATH, "r+", encoding="utf-8") as fh:
        content = fh.read()
        # Новые записи — сверху, сразу после заголовка: свежие баги видны первыми.
        marker_end = content.find("\n---\n")
        insert_at = marker_end + len("\n---\n") if marker_end != -1 else len(HEADER.rstrip("\n")) + 1
        if marker_end == -1 and not content.endswith("\n"):
            content += "\n"
            insert_at = len(content)
        fh.seek(0)
        fh.write(content[:insert_at] + entry + content[insert_at:])
        fh.truncate()
    return BUGS_PATH
