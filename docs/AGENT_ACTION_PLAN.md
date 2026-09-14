# AGENT_ACTION_PLAN.md — Neyronya / Нимфея

## Цель проекта
Локальная ИИ-стримерша Нимфея (близкие зовут Нима): живая девочка-лиса, считает
себя человеком, саркастичная цундере с чёрным/пошлым юмором. Голосовой диалог
(STT → LLM → TTS-клон), память, 3D-аватар (VRM) в прозрачном окне, Twitch-чат.
Ви́жена и управления компьютером НЕТ (решение пользователя, v13).

## Точки входа
- **Запуск — ТОЛЬКО через debug menu**: `debug menu.bat` → `[ ▶ ЗАПУСТИТЬ НИМФЕЮ ]`
  (ЗАПУСК/РЕСТАРТ/ВЫКЛЮЧИТЬ уже подключены к оркестратору `start.py`).
- `start.py` — оркестратор (composition root): собирает модули, главный цикл.
- Ручной ввод текста — debug menu → ИНСТРУМЕНТЫ → РУЧНОЙ ВВОД
  (очередь `cache/manual_commands.json`: «реплика» / «Нима: реплика» / «!regen …»).
- `vita_avatar_app/` — только VRM-модельки Нимы (`Nima_*.vrm`); Electron-окно — в `avatar/`.
- `docs/ARCHITECTURE.md` — архитектура (карта модулей, поток данных, бюджет железа).
- `docs/CHANGELOG.md` — история изменений (формат версий `## Нимфея vX.Y.Z — …`).

## Структура кода (v14)
Изолированные пакеты, у каждого свой README.md с инструкциями:
`core/` (config/лог/события/трасса) → `memory/ prompts/ llm/ stt/ tts/ avatar/
twitch/ ears/ voice_id/ web/ initiative/` → `pipeline/` (конвейер + нити) →
`start.py`. Импорты package-qualified (`from tts.tts_module import TTSModule`);
зависимости только вниз; `core/` ничего не импортирует из проекта.

## Ключевые модули
| Файл | Роль |
|------|------|
| `start.py` | Оркестратор: сборка модулей, прогрев LLM/TTS, главный цикл (ручной ввод, донаты) |
| `pipeline/pipeline.py` | DialoguePipeline: стриминг LLM→предложения→TTS-стрим, гёт ears, [ПОИСК], донаты |
| `pipeline/threads.py` | Нити разговора: переспросы и повторы с претензией |
| `pipeline/manual_control.py` | Очередь ручного ввода из debug menu |
| `ears/ears_module.py` | Гейт адресации: слышит всегда, отвечает по имени |
| `voice_id/voice_id_module.py` | Кто говорит: ECAPA-эмбеддинги → имена в память |
| `web/web_module.py` | ddgs-поиск: тег [ПОИСК: …] |
| `initiative/initiative_module.py` | Живая инициатива по тишине |
| `prompts/persona.py` | ПЕРСОНА Нимфеи — характер менять только здесь |
| `prompts/prompt_builder.py` | system-промпт (persona+память+нити), снимок в cache/last_prompt.txt |
| `memory/memory_module.py` | Единственный владелец memory.json: speaker/channel, голоса, гибридный recall |
| `llm/llm_module.py` | Ollama-клиент nimfea: generate/stream/micro |
| `stt/stt_module.py` | faster-whisper CPU: канал mic + loop (PyAudioWPatch), пауза при её речи |
| `tts/tts_module.py` | XTTS v2: клон, стрим предложений, липсинк, банворды |
| `avatar/bridge.py` | Мост Python→Electron (avatar_state.json), действия → VRMA |
| `avatar/renderer/viewer.js` | Рендерер аватара (источник истины; после правок — npm run build-vrm) |
| `twitch/twitch_module.py` | IRC-чат стримерши (включается env NIMA_TWITCH_*) |
| `twitch/twitch_donations.py` | Приёмник донатов: HTTP :8791 / data/donations_inbox.jsonl |
| `training/build_dataset.py` | Лора-датасет v2 из живой памяти |
| `core/pipeline_trace.py` | Трасса этапов → logs/pipeline_trace.jsonl (экраны ТРАССА) |
| `debug_menu/runtime_console.py` | ЗАПУСК/СТОП/РЕСТАРТ start.py, env Ollama, выгрузка VRAM |

## Правила работы
0. Модульные границы: правки — внутри одного пакета; новый функционал — в
   профильном пакете; `start.py` только связывает.
1. Не менять модели без причины (GGUF, референсы голоса).
2. Не добавлять библиотеки без обоснования.
3. `memory.json` не пересоздавать — только через `memory/`, атомарно.
4. Не выводить технический вывод в консоль пользователя.
5. После правок `avatar/renderer/viewer.js` — **обязательно** `cd avatar && npm run build-vrm`.
6. После правок Python — `py_compile`.
7. Обновлять CHANGELOG.md после каждого изменения (версии: 🟢 0.0.x / 🟡 0.x.0 / 🔴 x.0.0).
8. Обновлять ARCHITECTURE.md при смене потока данных.
9. Характер/вульгарность не стерилизовать (образ Нимфеи: сарказм, чёрный юмор,
   умеренный мат) — ограничивать только бессмысленную токсичность.
10. Весь вывод о персонаже — только «Нимфея»/«Нима» (никаких старых имён).

## Команды
```
# запуск (основной путь)
debug menu.bat → ЗАПУСК

# аватар: пересборка рендерера после правок
cd B:\Neyronya\avatar && npm run build-vrm

# Python-окружение: myenv/Scripts/python.exe
# проверка синтаксиса: py -3.12 -m py_compile <файлы>
```

## Текущая версия
v14.7.6

## Оставшиеся задачи
- [x] LoRA v2: переобучена в Colab (салат вылечен), nimfea подключена
      (models/nimfea_v2/, vision работает, NIMA_LLM_MODEL=nimfea). Хранилище
      Ollama перенесено на B:\OllamaModels (на C: кончилось место).
- [x] Латентность ответа (v14.7.6): компактная персона (PERSONA_COMPACT),
      RAG-топ (facts 6 / history 8 / recall 3), краткая метка vision
      (VISION_BRIEF). Промпт сжат ≈3.1× (9095→2896 симв.). Журнал —
      docs/LATENCY_OPTIMIZATION.md. Всё за env-флагами (откат в одну строку).
- [ ] Вторая партия LoRA-датасета: куратив dataset_v2 из живой памяти (build_dataset.py + curate.py)
- [ ] Экран «ЗАПРОС ПАМЯТИ» в debug menu поверх memory.query()
- [ ] Twitch: токен и канал для чата стримерши
- [ ] Отложено до карты 12+ ГБ: клон голоса (dots.tts); до тех пор Silero v5_5_ru + SSML-эмоции
