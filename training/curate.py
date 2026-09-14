"""curate.py — ручное ревью черновика датасета (шаг 2 из build_dataset.py).

Консольный разбор пар «пользователь → Нимфея»: бракуем тупые ответы,
правим не в характере, одобряем хорошее. Это главный источник качества
LoRA: её настоящая персона = то, что ты здесь одобрил и написал руками.

Запуск:
    myenv\\Scripts\\python.exe training\\curate.py

Клавиши:  y — оставить   n — брак   e — переписать ответ   s — пропустить
          q — сохранить и выйти
"""
from __future__ import annotations

import json
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parent
CANDIDATES_PATH = TRAINING_DIR / "dataset_candidates.jsonl"


def main() -> None:
    if not CANDIDATES_PATH.exists():
        print("Нет черновика. Сначала: python training/build_dataset.py")
        return
    rows = [json.loads(l) for l in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    stats = {"ok": 0, "bad": 0}
    for row in rows:
        if row.get("verdict") in ("ok", "bad"):
            stats[row["verdict"]] += 1
    print(f"Всего {len(rows)} пар; уже разобрано: ok={stats['ok']} bad={stats['bad']}. "
          f"Показываю неразобранные.\n")
    changed = 0
    for row in rows:
        if row.get("verdict") in ("ok", "bad"):
            continue
        print("─" * 60)
        print(f"#{row['id']} [{row.get('source', '?')}]")
        print(f"  ЮЗЕР: {row['user'][:160]}")
        print(f"  НИМА: {row['assistant'][:160]}")
        while True:
            cmd = input("  [y/n/e/s/q] > ").strip().lower()
            if cmd == "y":
                row["verdict"] = "ok"; changed += 1; break
            if cmd == "n":
                row["verdict"] = "bad"; changed += 1; break
            if cmd == "e":
                fixed = input("  ПРАВИЛЬНЫЙ ОТВЕТ: ").strip()
                if fixed:
                    row["assistant"] = fixed
                    row["verdict"] = "ok"; changed += 1
                break
            if cmd == "s":
                break
            if cmd == "q":
                _save(rows)
                print("Сохранено. Продолжишь позже — прогресс не теряется.")
                return
    _save(rows)
    print(f"\nГотово: разобрано в этом сеансе {changed}. Финал: "
          f"python training/build_dataset.py --final")


def _save(rows: list[dict]) -> None:
    with CANDIDATES_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
