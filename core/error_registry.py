"""Реестр кодов ошибок Нимфеи.

Журнал: logs/error_journal.jsonl — записи {"ts", "code", "message"}.
Пишется автоматически: install_journal_handler() ставит logging.Handler,
который ловит ERROR-сообщения с кодом вида [E-XXX-NNN] в тексте.
debug_menu/error_viewer.py читает журнал и каталог ERROR_CODES отсюда.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path

from .config import LOGS_DIR

JOURNAL_PATH = LOGS_DIR / "error_journal.jsonl"

CODE_RE = re.compile(r"\[(E-[A-Z]+-\d{3})\]")

# Каталог кодов: что сломалось и где искать. Коды вне каталога резолвятся
# с пометкой «не из каталога», но всё равно показываются из журнала.
ERROR_CODES: dict[str, dict[str, str]] = {
    "E-LLM-001": {"desc": "LLM не ответил (таймаут/пустой ответ)",
                  "where": "llm/, logs/technical_context.log"},
    "E-LLM-002": {"desc": "LLM вернул ответ не в JSON-протоколе",
                  "where": "llm/ (парсер протокола), промпт"},
    "E-TTS-001": {"desc": "TTS не синтезировал аудио",
                  "where": "tts/, референс data/voice_ref/"},
    "E-STT-001": {"desc": "STT не распознал аудио",
                  "where": "stt/, микрофонное устройство"},
    "E-AVA-001": {"desc": "Окно аватара не запустилось или упало",
                  "where": "avatar/main.cjs, logs/ring_electron.log"},
    "E-MIC-001": {"desc": "Микрофон не открылся/тишина на входе",
                  "where": "stt/ устройства, настройки Windows"},
    "E-MEM-001": {"desc": "Ошибка чтения/записи памяти",
                  "where": "memory/, memory.json"},
    "E-UNK-000": {"desc": "Неизвестная ошибка без кода",
                  "where": "logs/technical_context.log"},
}

_lock = threading.Lock()
_installed = False


def _append(code: str, message: str) -> None:
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
        "message": message[:500],
    }
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with _lock, JOURNAL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


class _JournalHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        for code in CODE_RE.findall(msg):
            _append(code, msg)


def install_journal_handler() -> None:
    """Подключить автозапись кодов из root-логгера (вызывается из setup_logging)."""
    global _installed
    if _installed:
        return
    _installed = True
    handler = _JournalHandler(level=logging.ERROR)
    logging.getLogger().addHandler(handler)


def record(code: str, message: str) -> None:
    """Ручная запись кода в журнал (кроме автоматической из логгера)."""
    _append(code.upper(), message)


def recent(limit: int = 100) -> list[dict]:
    """Последние записи журнала, новые в конце."""
    if not JOURNAL_PATH.exists():
        return []
    try:
        lines = JOURNAL_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def stats() -> dict[str, int]:
    """Сколько раз встречался каждый код."""
    agg: dict[str, int] = {}
    for entry in recent(limit=10_000):
        agg[entry.get("code", "E-UNK-000")] = agg.get(entry.get("code", "E-UNK-000"), 0) + 1
    return agg


def resolve(code: str) -> dict[str, str]:
    """Расшифровка кода из каталога (или заглушка «не из каталога»)."""
    code = code.strip().upper()
    if code in ERROR_CODES:
        return {"code": code, **ERROR_CODES[code]}
    return {"code": code, "desc": "код не из каталога", "where": "logs/technical_context.log"}


def catalog() -> dict[str, dict[str, str]]:
    return dict(ERROR_CODES)


def clear_journal() -> int:
    """Стереть журнал. Возвращает число удалённых записей."""
    if not JOURNAL_PATH.exists():
        return 0
    try:
        n = len(JOURNAL_PATH.read_text(encoding="utf-8").splitlines())
        JOURNAL_PATH.unlink()
        return n
    except OSError:
        return 0
