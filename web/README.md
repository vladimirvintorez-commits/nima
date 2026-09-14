# web — интернет Нимфеи

Нимфея умеет искать в интернете (требование 16):

1. **Она сама просит поиск** — отвечает тегом `[ПОИСК: запрос]`, пайплайн
   находит результаты и даёт ей второй ход с ними (протокол простых тегов:
   3b-модель tool-calls не тянет, тег — надёжно).
2. **Она спросила — ответа не было** `NIMA_WEB_UNANSWERED_SEC` секунд →
   ищет ответ сама и приносит его (связка с нитями разговора).

Поисковик: `ddgs` (DuckDuckGo, без API-ключа), регион ru-ru.

```python
from web import search, format_results, extract_search_tag

query = extract_search_tag(reply)          # тег из ответа LLM
results = search(query)                    # [{title, url, snippet}]
block = format_results(query, results)     # блок для промпта
```

Отключение: `NIMA_WEB_SEARCH=0`. Установка: `pip install ddgs`.
