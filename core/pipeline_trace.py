"""pipeline_trace — трассировка этапов пайплайна → logs/pipeline_trace.jsonl.

Экраны debug menu «ТРАССА ЗАПРОСА» / «ТРАССА ОЗВУЧКИ» читают этот файл через
read_recent() + group_by_request(). Трасса копеечная: одна JSONL-строка на этап.

Использование:
    import core.pipeline_trace as trace
    rid = trace.new_request()
    trace.mark("in", rid, text="привет")
    trace.mark("prompt", rid, info="8192 токенов")
    trace.mark("tts_start", rid, text="первое предложение…")

Этапы (см. _STAGE_LABELS в debug_menu/dev_tools.py):
    in → prompt → llm_start → llm_done → postprocess → tts_start → tts_done
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from core.config import LOGS_DIR

TRACE_PATH = LOGS_DIR / "pipeline_trace.jsonl"

_lock = threading.Lock()


def new_request() -> str:
    return secrets.token_hex(4)


def mark(stage: str, request_id: str | None = None, *, text: str = "",
         info: str = "") -> None:
    """Отметить этап. delta_ms — время от ПРЕДЫДУЩЕГО этапа этого же запроса."""
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "request_id": request_id or "-",
        "stage": stage,
        "delta_ms": 0,
        "total_ms": 0,
    }
    if text:
        entry["text"] = text[:200]
    if info:
        entry["info"] = info[:200]
    with _lock:
        entry["delta_ms"], entry["total_ms"] = _delta(request_id or "-", time.perf_counter())
        _append(entry)


_last: dict[str, float] = {}
_first: dict[str, float] = {}


def _delta(rid: str, now: float) -> tuple[int, int]:
    first = _first.setdefault(rid, now)
    prev = _last.get(rid, first)
    _last[rid] = now
    # защита от бесконечного роста словарей (долгие сессии)
    if len(_last) > 256:
        cutoff = now - 3600
        for key in [k for k, v in _last.items() if v < cutoff]:
            _last.pop(key, None)
            _first.pop(key, None)
    return int((now - prev) * 1000), int((now - first) * 1000)


def _append(entry: dict) -> None:
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"pipeline_trace: {exc}")  # логгер тут не нужен — best effort


def read_recent(limit: int = 400) -> list[dict]:
    """Последние limit записей (в хронологическом порядке)."""
    try:
        with open(TRACE_PATH, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]
    except FileNotFoundError:
        return []
    except Exception:
        return []


def group_by_request(entries: list[dict]) -> list[dict]:
    """Сгруппировать записи в цепочки по request_id:
    [{request_id, stages: [{stage, delta_ms, text, info}], total_ms}]"""
    chains: dict[str, dict] = {}
    order: list[str] = []
    for entry in entries:
        rid = str(entry.get("request_id", "-"))
        if rid not in chains:
            chains[rid] = {"request_id": rid, "stages": [], "total_ms": 0}
            order.append(rid)
        chain = chains[rid]
        chain["stages"].append({
            "stage": entry.get("stage", "?"),
            "delta_ms": entry.get("delta_ms", 0),
            "text": entry.get("text", ""),
            "info": entry.get("info", ""),
        })
        chain["total_ms"] = max(chain["total_ms"], int(entry.get("total_ms", 0)))
    return [chains[rid] for rid in order]


def reset_timers() -> None:
    _last.clear()
    _first.clear()
