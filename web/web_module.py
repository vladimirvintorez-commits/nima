"""web — интернет-поиск для Нимфеи (требование 16).

Движок: ddgs (DuckDuckGo, без API-ключа). Если пакет недоступен — мягкая
деградация (пайплайн честно скажет «интернет не отвечает»).

Два пути использования (протокол простых тегов — 3b-модель функции не тянет):
  1. LLM просит поиск сама: отвечает «[ПОИСК: запрос]» → пайплайн ищет и
     делает второй ход с результатами;
  2. правило «спросила — ответа нет N минут → нашла сама» (см. pipeline).

Настройки: NIMA_WEB_SEARCH (0/1), NIMA_WEB_RESULTS.
"""
from __future__ import annotations

import logging
import re

from core.config import WEB_ENABLED, WEB_RESULTS

log = logging.getLogger("web")

SEARCH_TAG_RE = re.compile(r"\[\s*ПОИСК\s*[:：]\s*(.+?)\s*\]", re.IGNORECASE)


def extract_search_tag(text: str) -> str | None:
    """«[ПОИСК: запрос]» из ответа LLM → запрос (и очистка текста от тега)."""
    match = SEARCH_TAG_RE.search(text)
    return match.group(1).strip() if match else None


def strip_search_tags(text: str) -> str:
    return SEARCH_TAG_RE.sub("", text).strip()


def search(query: str, limit: int = WEB_RESULTS) -> list[dict]:
    """Топ-результаты [{title, url, snippet}] или [] при неудаче."""
    if not WEB_ENABLED or not query.strip():
        return []
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # старое имя пакета
        except ImportError:
            log.warning("[WARNING] web: пакет ddgs не установлен — поиск недоступен")
            return []
    except Exception as exc:  # noqa: BLE001
        log.warning("[WARNING] web: %s", exc)
        return []
    try:
        out = []
        with DDGS() as ddgs:
            for hit in ddgs.text(query, region="ru-ru", max_results=limit):
                out.append({
                    "title": str(hit.get("title", "")).strip(),
                    "url": str(hit.get("href", "")).strip(),
                    "snippet": str(hit.get("body", "")).strip(),
                })
        log.info("web: %r → %d результатов", query, len(out))
        return out
    except Exception as exc:  # noqa: BLE001
        log.error("[ERROR] web-поиск %r: %s", query, exc)
        return []


def format_results(query: str, results: list[dict]) -> str:
    """Результаты поиска → текстовый блок для промпта."""
    if not results:
        return ""
    lines = [f"Результаты поиска в интернете по запросу «{query}»:"]
    for i, hit in enumerate(results, 1):
        snippet = hit["snippet"][:300]
        lines.append(f"{i}. {hit['title']} ({hit['url']})\n   {snippet}")
    lines.append("Используй эти факты, отвечай своими словами и не выдумывай сверх них.")
    return "\n".join(lines)
