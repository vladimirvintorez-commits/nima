"""Ручной ввод из debug menu: файловая очередь cache/manual_commands.json.

Протокол (экран РУЧНОЙ ВВОД в debug menu):
  • «реплика»        — обрабатывается как реплика пользователя (LLM отвечает);
  • «Нима: реплика»  — Нимфея проговаривает текст сразу, минуя LLM;
  • «!regen текст»   — повтор последнего ввода (спец-маркер debug menu).
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from core.config import MANUAL_LAST_PATH, MANUAL_QUEUE_PATH


def _read_queue() -> list:
    try:
        data = json.loads(MANUAL_QUEUE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _write_queue(items: list) -> None:
    MANUAL_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANUAL_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, MANUAL_QUEUE_PATH)


def enqueue(text: str) -> None:
    items = _read_queue()
    items.append({"text": text, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
    _write_queue(items[:200])


def enqueue_cmd(cmd: dict) -> None:
    """Структурная команда от debug menu (не реплика): напр.
    {"cmd": "enroll_voice", "name": "Кизилл", "wav": "cache/enroll.wav"}.
    Выполняет пайплайн/оркестратор, результат — в cache/enroll_result.json."""
    items = _read_queue()
    entry = {"cmd": cmd, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    items.append(entry)
    _write_queue(items[:200])


def remember_last(text: str) -> None:
    MANUAL_LAST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANUAL_LAST_PATH.write_text(text, encoding="utf-8")


def regenerate_last() -> str:
    try:
        return MANUAL_LAST_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def drain_entries() -> list[dict]:
    """Забирает всю очередь целиком (реплики + структурные команды)."""
    items = _read_queue()
    if not items:
        return []
    _write_queue([])
    out = []
    for entry in items:
        if isinstance(entry, dict) and entry.get("cmd"):
            out.append(entry)
        elif str((entry or {}).get("text", "")).strip():
            out.append({"text": str(entry["text"]).strip()})
    return out


def drain() -> list[str]:
    """Только реплики (совместимость со старыми вызовами)."""
    return [e["text"] for e in drain_entries() if e.get("text")]
