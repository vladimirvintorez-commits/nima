# models — модели Нимфеи

| Файл | Что это |
|------|---------|
| `nimfea_full/` | Активная связка: `nimfea.Q4_K_M.gguf` + `mmproj-model-f16.gguf` (gemma3 vision tower от ggml-org, LoRA его не трогала) + Modelfile (`FROM .`). `ollama create nimfea -f models/nimfea_full/Modelfile` — регистрирует модель СО ЗРЕНИЕМ (v14.8.46) |
| `nimfea.Q4_K_M.gguf` | **АКТИВНАЯ** (v14.8.45) — gemma-3-4b-it + LoRA v7, Q4_K_M ~2.5 ГБ, шаблон gemma3 `<start_of_turn>`. Экспорт: merged_16bit → convert_hf_to_gguf (f16) → llama-quantize Q4_K_M (unsloth save_pretrained_gguf на gemma3 глючный — НЕ использовать) |
| `Modelfile` | Рецепт регистрации активной модели (`FROM ./nimfea.Q4_K_M.gguf`, шаблон gemma3, stops `<end_of_turn>`/`<eos>`) |
| `nimfea-q4_k_m.gguf` | Аварийный откат: qwen2.5:3b + LoRA v6 (1.9 ГБ). Регистрация: `ollama create nimfea -f models/Modelfile.nimfea` |

## Регистрация / обновление модели в Ollama

```
cd models
ollama create nimfea -f Modelfile
ollama show nimfea --modelfile | findstr TEMPLATE   # должно быть <start_of_turn>!
ollama run nimfea "Привет, Нима!"                    # проверка характера
```

Проверка после регистрации:
- `ollama ps` → модель ~2.5-2.7 ГБ, 100% GPU;
- характер: ответы 1-3 предложения, создатель Кизилл, без «я ассистент».

**Зрение (v14.8.46):** nimfea зарегистрирована с mmproj — capability
`vision`, скриншоты через /api/chat images работают (проверено живьём).
Модель целиком ~2.9 ГБ VRAM 100% GPU.

## Хранилище Ollama — на B: (v14.7.0)

На C: было 1.9 ГБ свободно — регистрация модели падала
(`failed to validate GGUF ... ios_base::clear`) на записи временного файла.
Хранилище перенесено:

```
setx OLLAMA_MODELS "B:\OllamaModels"    # blobs+manifests прямо в корне
```

После переустановки/обновления Ollama переменная сохранится (setx, пользовательская).

## Если ollama create снова падает на валидации

Ollama валидирует GGUF своим llama.cpp; файлы от более свежих сборок могут не
проходить. Лекарство — перемквантовать тем же llama.cpp, что конвертировал
(инструменты в `tools/llamacpp/`, сборка b10798):

```
tools\llamacpp\llama-quantize.exe model.Q4_K_M.gguf model.fixed.gguf Q4_K_M
```

(Урок v14.7: requant тем же b10798 НЕ вылечил валидацию — реальная причина
была в нехватке места на C:. Сначала проверить диск!)

## Бюджет VRAM (GTX 1660 Super, 6 ГБ)

- nimfea Q4_K_M ~2.7 ГБ — основной, запас ~1.5 ГБ (не хватает до порога
  CUDA-версии STT 1.8 ГБ; вариант — урезать LLM_NUM_CTX до 6k).
- gemma3:4b при активной nimfea из VRAM вытеснится (это ок: она запасная,
  осталась для возможного зрительного фолбэка).
- XTTS клона рядом не влезает (урок v14.3) — Silero на CPU.
