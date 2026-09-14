# llm — модуль LLM (дообученная модель Нимфеи)

Локальная дообученная модель `models/nimfea-q4_k_m.gguf` (qwen2.5:3b + LoRA,
датасет v6) через Ollama. В Ollama модель зарегистрирована под именем `nimfea`.

| Файл | Роль |
|------|------|
| `llm_module.py` | Клиент Ollama: `warmup()` (ретраи при гонке VRAM), `generate()`, `generate_stream()` |

## Изоляция

Модуль не знает про память/промпты/голос: получает готовые `messages`,
возвращает текст. Ошибка → `None` + запись в `self.last_error`, пайплайн сам
решает, что делать (ставит заглушку-фразу).

## Как пересобрать/обновить модель

1. Новый GGUF положить в `models/` (например `nimfea-q4_k_m.gguf`).
2. При необходимости поправить `models/Modelfile.nimfea`.
3. `cd models && ollama create nimfea -f Modelfile.nimfea`
4. Проверка: `ollama run nimfea "Привет, Нима!"`

## Настройки (core/config.py)

`NIMA_LLM_MODEL` (nimfea), `NIMA_OLLAMA_URL`, `NIMA_LLM_NUM_CTX` (8192),
`NIMA_LLM_TIMEOUT`. Ollama держит модель в VRAM ~2 ГБ — рядом помещается XTTS
(~2 ГБ) в 6 ГБ GTX 1660 Super; **не грузить другие модели одновременно**
(урок v11: nimfea вытеснялась и падала с 500).
