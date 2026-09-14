"""Одноразовая очистка memory.json от мусора веб-поиска и ложных people.

Ничего НЕ удаляет физически (урок: не терять память) — только:
  * деактивирует факты (active=false) и вешает тег legacy_junk на:
      - факты, начинающиеся с "По интернет-поиску" (свалка веб-поиска);
      - факты вида "Пользователя нужно называть: <слово>" (ложные имена);
      - вопросы/обрывки реплик, ошибочно записанные как факты.
  * чистит people от ложных ключей (не-имена: 'вчера' и т.п.).
Делает резервную копию memory.json.backup_cleanup перед записью.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MEM = ROOT / "memory.json"
BACKUP = ROOT / "memory.json.backup_cleanup"

# признаки мусорного "факта"
_JUNK_PREFIXES = ("По интернет-поиску", "Пользователя нужно называть:")
# факты-обрывки (вопросы/просьбы вспомнить) — не настоящие факты о мире
_JUNK_SUBSTR = (
    "помнишь", "запомни", "напомни", "вспомни", "моё имя", "мою помнишь",
    "имя моё", "что ты помнишь", "что я тебе", "что я говорил", "что я сказал",
)

# какие ключи people оставить (настоящие имена людей окружения).
# всё остальное — мусор из неверного парсинга.
_PEOPLE_KEEP: set[str] = set()  # сейчас в people только мусор → чистим всё

# Белый список настоящих фактов, которые оставляем активными (точный текст,
# начало строки). Всё остальное среди "активных" — обрывки реплик → в junk.
_FACT_KEEP_PREFIXES = (
    "Пользователя зовут Кизил",
    "Пользователь Кизил",
    "Нимфея относится к Кизилу",
    "Нимфею зовут Нимфея",
    "Мне нравится покружись",
)

# Починка user_context: настоящее имя создателя — Кизил (мужской).
_USER_CONTEXT = {"name": "Кизил", "gender": "мужской"}


def _is_junk_fact(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if t.startswith(_JUNK_PREFIXES):
        return True
    low = t.lower()
    # короткие обрывки-вопросы про память
    if len(t) < 60 and any(s in low for s in _JUNK_SUBSTR):
        return True
    return False


def main() -> None:
    data = json.loads(MEM.read_text(encoding="utf-8"))
    if not BACKUP.exists():
        shutil.copy2(MEM, BACKUP)
    layers = data.setdefault("layers", {})

    facts = layers.get("facts", [])
    deactivated = 0
    for f in facts:
        text = f.get("text", "")
        keep = text.strip().startswith(_FACT_KEEP_PREFIXES)
        if keep:
            continue
        if _is_junk_fact(text) or not keep:
            if f.get("active", True) is not False or "legacy_junk" not in (f.get("tags") or []):
                deactivated += 1
            f["active"] = False
            tags = set(f.get("tags") or [])
            tags.add("legacy_junk")
            f["tags"] = sorted(tags)

    active_left = [f for f in facts if f.get("active", True)]

    # Починка контекста пользователя (было ложное имя от веб-поиска).
    layers["user_context"] = dict(_USER_CONTEXT)

    people = layers.get("people", {})
    removed_people = [k for k in list(people.keys()) if k not in _PEOPLE_KEEP]
    for k in removed_people:
        people.pop(k, None)

    MEM.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"facts total       : {len(facts)}")
    print(f"deactivated now   : {deactivated}")
    print(f"active facts left : {len(active_left)}")
    for f in active_left:
        print("  ACTIVE:", repr(f.get("text", "")[:80]), f.get("tags"))
    print(f"people removed    : {removed_people}")
    print(f"backup saved to   : {BACKUP.name}")


if __name__ == "__main__":
    main()
