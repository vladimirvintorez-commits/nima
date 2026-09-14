"""Единый логгер Нимфеи.

Пишет в logs/technical_context.log (utf-8) и в stderr. Формат совместим с
debug menu: строки с [ERROR] / [WARNING] красятся в КОНСОЛИ автоматически,
реплики Нимфы помечаются тегом `[SpeakText] source=nima:* text=...`.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys

from .config import LOGS_DIR, LOG_PATH

_configured = False


class _SpeakFilter(logging.Filter):
    """Гасит WARNING/ERROR-дубли от логгера speak — реплики уже видны отдельно."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        return True


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=8 * 1024 * 1024, backupCount=2, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)

    from .error_registry import install_journal_handler
    install_journal_handler()


def speak_log(text: str, source: str = "nima:llm") -> None:
    """Реплика Нимфы в лог — КОНСОЛЬ в debug menu подсвечивает её отдельно."""
    logging.getLogger("speak").info("[SpeakText] source=%s text=%s", source, text)
