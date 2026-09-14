"""Разовая чистка голосовых профилей-заглушек из memory.json (13.09.2026).

Живой тест накопил 47 «Друг N»/«Собеседник», в том числе из голоса владельца
(до правила «микрофон = владелец» в voice_id). Профили-заглушки удаляются,
НО не молча: всё, что удаляется, складывается в бэкап
data/people_removed_<дата>.json — ничего не теряется.

Запуск: py -3 tools/cleanup_voice_profiles.py  (Нимфея должна быть выключена!)
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
MEMORY_PATH = PROJECT / "memory.json"
PLACEHOLDER_RE = re.compile(r"^(?:собеседник|гость|незнаком\w*|друг\s*\d+|friend\s*\d+)$",
                            re.IGNORECASE)


def main() -> None:
    if not MEMORY_PATH.exists():
        print("memory.json не найден")
        sys.exit(1)
    data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    people = data.get("layers", {}).get("people", {})
    removed = {}
    for key, person in list(people.items()):
        name = str((person or {}).get("name") or key).strip()
        if PLACEHOLDER_RE.match(name):
            removed[key] = person
            del people[key]
    if not removed:
        print("Профили-заглушки не найдены — чистить нечего.")
        return
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = PROJECT / "data" / f"people_removed_{stamp}.json"
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(removed, ensure_ascii=False, indent=1), encoding="utf-8")
    MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Удалено {len(removed)} профилей-заглушек, бэкап: {backup}")


if __name__ == "__main__":
    main()
