"""Вкладка «Задачи» в debug menu: добавление задач в docs/TASKS_IN_PROGRESS.md.

Задача пишется текстом, помечается сложностью (🟢 простая / 🟡 средняя / 🔴 сложная)
и автоматически вставляется в нужный раздел файла — с правильным чекбоксом и
нумерацией версий по цветам (🟢 = patch 0.0.x, 🟡 = minor 0.x.0, 🔴 = major x.0.0).
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import PROJECT_ROOT

TASKS_PATH = PROJECT_ROOT / "docs" / "TASKS_IN_PROGRESS.md"

SECTIONS = {
    "green": {
        "marker": "## 🟢 Зелёные",
        "label": "🟢 Простая (patch 0.0.x)",
    },
    "yellow": {
        "marker": "## 🟡 Жёлтые",
        "label": "🟡 Средняя (minor 0.x.0)",
    },
    "red": {
        "marker": "## 🔴 Красные",
        "label": "🔴 Сложная (major x.0.0)",
    },
}


def load_tasks_text() -> str:
    try:
        return TASKS_PATH.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _section_range(text: str, marker: str):
    start = text.find(marker)
    if start == -1:
        return None
    next_section = re.search(r"\n## ", text[start + len(marker):])
    end = start + len(marker) + next_section.start() + 1 if next_section else len(text)
    return start, end


def add_task(description: str, level: str = "green") -> Path:
    description = " ".join(str(description or "").split()).strip().rstrip("-")
    if not description:
        raise ValueError("Пустое описание задачи")
    section = SECTIONS.get(level)
    if not section:
        raise ValueError(f"Неизвестный уровень сложности: {level}")
    text = load_tasks_text()
    if not text:
        raise RuntimeError("TASKS_IN_PROGRESS.md не найден")
    span = _section_range(text, section["marker"])
    if not span:
        raise RuntimeError(f"Раздел «{section['marker']}» не найден в TASKS_IN_PROGRESS.md")
    start, end = span
    block = text[start:end]
    # Вставляем перед первым встреченным разделителем "---" внутри секции,
    # иначе — в конец секции.
    divider = block.find("\n---")
    insert_pos = start + divider if divider != -1 else end
    entry = f"- [ ] **{description}**\n"
    new_text = text[:insert_pos].rstrip("\n") + "\n" + entry + "\n" + text[insert_pos:].lstrip("\n")
    TASKS_PATH.write_text(new_text, encoding="utf-8")
    return TASKS_PATH


def count_tasks() -> dict[str, int]:
    """Открытые задачи по секциям — для сводки на экране."""
    text = load_tasks_text()
    counts = {}
    for key, section in SECTIONS.items():
        span = _section_range(text, section["marker"])
        if not span:
            counts[key] = 0
            continue
        block = text[span[0]:span[1]]
        counts[key] = len(re.findall(r"^- \[ \]", block, flags=re.MULTILINE))
    return counts
