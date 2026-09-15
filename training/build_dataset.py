"""build_dataset.py — датасет для LoRA nimfea с РУЧНОЙ курацией (требование 20).

Референс Neuro-sama: модель маленькая — качество дают обвесы и дообучение.
Урок v14: датасет, собранный целиком из живой памяти, обучил модель «бреду» —
в память попадают донаты, обрывки чужой речи и неудачные ответы. Поэтому
сборка теперь трёхшаговая, с человеком в цикле:

  1) ЧЕРНОВИК из памяти:
     myenv\\Scripts\\python.exe training\\build_dataset.py
     → training/dataset_candidates.jsonl  (verdict: "" — не разобрано)

  2) РЕВЬЮ (вручную или через curate.py):
     для каждой пары выставить verdict: "ok" / "bad", ответ можно ПРАВИТЬ —
     так в датасет попадает её НАСТОЯЩАЯ персона, а не случайная реплика.

  3) ФИНАЛ (+ руки-эталоны из manual_pairs.jsonl, вес ×5):
     myenv\\Scripts\\python.exe training\\build_dataset.py --final
     → training/dataset_v2.jsonl  (готов к LoRA-обучению, training/README.md)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEMORY_JSON = PROJECT_ROOT / "memory.json"
TRAINING_DIR = Path(__file__).resolve().parent
CANDIDATES_PATH = TRAINING_DIR / "dataset_candidates.jsonl"
MANUAL_PATH = TRAINING_DIR / "manual_pairs.jsonl"
OUT_PATH = TRAINING_DIR / "dataset_v2.jsonl"

sys.path.insert(0, str(PROJECT_ROOT))
from prompts.persona import PERSONA  # noqa: E402

# Реплики с этими источниками в датасет не попадают вовсе (не диалог с человеком)
_SKIP_SOURCES = {"initiative", "threads"}
# Сколько раз повторить эталонную пару из manual_pairs.jsonl (вес против мусора)
MANUAL_WEIGHT = 5

# --- Авто-префильтр черновика (v14.8.53) ---------------------------------
# Урок: даже во «второй партии» из живой памяти встречаются заведомо негодные
# пары — они зря съедают время курации, а проскочив в датасет, закрепляют
# «тупость» (мусор на входе → мусор на выходе). Явный мусор помечаем
# verdict="bad" сразу: curate.py такие пропускает (пропускает уже размеченные),
# то есть до глаз доходит только то, что реально стоит судить. Пороги мягкие —
# режем лишь очевидное, спорное оставляем человеку (verdict="").
_MIN_ASSISTANT_CHARS = 2      # пустой/односимвольный ответ — не пример
_MAX_ASSISTANT_CHARS = 600    # простыня — не в характере (её норма 1-3 фразы)
_MIN_USER_CHARS = 2           # без реплики юзера пара бессмысленна


def _looks_bad(user: str, assistant: str) -> str | None:
    """Причина забраковать пару автоматически, или None если ок/сомнительно."""
    u, a = user.strip(), assistant.strip()
    if len(a) < _MIN_ASSISTANT_CHARS:
        return "пустой ответ"
    if len(u) < _MIN_USER_CHARS:
        return "пустая реплика юзера"
    if len(a) > _MAX_ASSISTANT_CHARS:
        return "простыня (не 1-3 фразы)"
    if a.lower() == u.lower():
        return "эхо вопроса"
    if not any(ch.isalpha() for ch in a):
        return "ответ без слов"
    return None


def collect_pairs() -> list[dict]:
    """Черновые пары [(user, assistant, source)] из живой истории memory.json."""
    data = json.loads(MEMORY_JSON.read_text(encoding="utf-8"))
    dialog = data.get("layers", {}).get("dialog", [])
    pairs = []
    pending_user: dict | None = None
    for entry in dialog:
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        role = entry.get("role")
        source = str(entry.get("user", "")).split(":")[0]
        if role == "user":
            pending_user = {"text": text, "source": source,
                            "speaker": entry.get("speaker", "")}
        elif role == "bot" and pending_user:
            if pending_user["source"] not in _SKIP_SOURCES:
                pairs.append({"user": pending_user["text"], "assistant": text,
                              "source": pending_user["source"]})
            pending_user = None
    return pairs


def dedupe(pairs: list[dict]) -> list[dict]:
    """Дубликаты (анти-галлюцинация v13.1.0) в датасете вредят — убираем."""
    seen: set[tuple[str, str]] = set()
    out = []
    for p in pairs:
        key = (p["user"].lower()[:200], p["assistant"].lower()[:200])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def build_candidates() -> None:
    pairs = dedupe(collect_pairs())
    auto_bad = 0
    with CANDIDATES_PATH.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(pairs):
            reason = _looks_bad(p["user"], p["assistant"])
            if reason:
                auto_bad += 1
            fh.write(json.dumps({
                "id": i, "user": p["user"], "assistant": p["assistant"],
                "source": p["source"],
                "verdict": "bad" if reason else "",   # "" | ok | bad
                "auto": reason or "",                 # причина авто-брака (для аудита)
            }, ensure_ascii=False) + "\n")
    print(f"Черновик: {len(pairs)} пар → {CANDIDATES_PATH}")
    print(f"Авто-брак (явный мусор, курация его не показывает): {auto_bad}; "
          f"на ручной разбор: {len(pairs) - auto_bad}.")
    print("Дальше: разбор через training/curate.py (или руками: verdict ok/bad, ответ можно править).")


def write_dataset(rows: list[dict]) -> None:
    with OUT_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            record = {
                "messages": [
                    {"role": "system", "content": PERSONA.strip()},
                    {"role": "user", "content": row["user"]},
                    {"role": "assistant", "content": row["assistant"]},
                ],
                "meta": {"source": row.get("source", "manual")},
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_final() -> None:
    rows: list[dict] = []
    used_candidates = 0
    if CANDIDATES_PATH.exists():
        for line in CANDIDATES_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("verdict") == "ok":
                rows.append({"user": rec["user"], "assistant": rec["assistant"],
                             "source": rec.get("source", "curated")})
                used_candidates += 1
    manual = 0
    if MANUAL_PATH.exists():
        for line in MANUAL_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("user") and rec.get("assistant"):
                rows += [{"user": rec["user"], "assistant": rec["assistant"],
                          "source": "manual"}] * MANUAL_WEIGHT
                manual += 1
    write_dataset(rows)
    print(f"Финал: {len(rows)} записей "
          f"(курированных {used_candidates} + эталонных {manual}×{MANUAL_WEIGHT}) → {OUT_PATH}")
    print("Дальше: training/README.md — LoRA-обучение и `ollama create nimfea`.")


if __name__ == "__main__":
    if "--final" in sys.argv:
        build_final()
    else:
        build_candidates()
