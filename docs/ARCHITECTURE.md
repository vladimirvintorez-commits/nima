# ARCHITECTURE.md — Neyronya / Нимфея (v14.0.0 05.09.2026)

> v14 — «КОСТЯК» по 21 требованию (см. `docs/SKELETON.md`): стриминговый
> конвейер (LLM → предложения → TTS-стрим), loopback-слух (собеседник из
> созвона), гейт адресации (слышит всегда — отвечает по имени), voice_id
> (кто говорит по голосу), веб-поиск по тегу `[ПОИСК: …]`, нити разговора
> (переспросы/претензии), живая инициатива, память v3 (speaker/channel +
> гибридный recall), VRMA-анимации, приёмник донатов. Модель LLM не менялась.
> Персонаж везде — **Нимфея / Нима**.

## 1. Карта проекта

```
B:\Neyronya\
├── debug menu.bat          # запуск debug menu (pythonw debug_menu/archive_gui.py)
├── start.py                # ОРКЕСТРАТОР (composition root): только связывание модулей
├── requirements.txt        # зависимости рантайма (venv myenv/)
├── memory.json             # ПАМЯТЬ Нимфеи — владелец только memory/ (не трогать руками)
├── Референс 1.wav          # ┐ референсы голоса для TTS-клона (XTTS)
├── Референс 2.wav          # ┘
├── core/                   # конфиг (NIMA_*), единый лог, шина событий, pipeline_trace
├── memory/                 # модуль памяти → memory.json + голоса + векторный recall
├── prompts/                # персона Нимфеи + построитель промптов (cache/last_prompt.txt)
├── llm/                    # Ollama-клиент: модель nimfea (gemma3+LoRA v7 + mmproj
│                           #   со v14.8.46; стрим + микро-классификатор + зрение)
├── stt/                    # faster-whisper CPU: канал mic + канал loop (PyAudioWPatch)
├── ears/                   # гейт адресации: отвечает только на «Нимфея/Нима…»
├── voice_id/               # speechbrain ECAPA: кто говорит → имена в память
│                           #   (v14.8.16: канал mic = владелец VOICE_ID_USER,
│                           #    «Друг N» заводятся только с loop-канала)
├── tts/                    # Silero v5_5_ru (CPU, основной с v14.3) + XTTS/Chatterbox
│                           #   как опции бэкенда (NIMA_TTS_BACKEND)
├── web/                    # ddgs-поиск: тег [ПОИСК: …] + поиск ДО ответа по
│                           #   словесным триггерам (v14.8.47: nimfea v7 не ставит
│                           #   тег — пайплайн ищет сам и кладёт результаты в system)
├── pipeline/               # DialoguePipeline (стриминг) + threads (нити) + manual_control
│                           #   (v14.8.46: внутренние реплики — зрение/инициатива/нити —
│                           #   не отменяют начатый ответ пользователю, _run_async(priority))
├── initiative/             # живая инициатива: тема из памяти/веба/вопросов по тишине
├── avatar/                 # Electron: прозрачное окно, VRM + VRMA, две точки управления
├── twitch/                 # чат стримерши (IRC) + twitch_donations (HTTP :8791 / ящик)
├── training/               # build_dataset.py (лора-датасет v2) + README по LoRA
├── debug_menu/             # меню разработки: ЗАПУСК/РЕСТАРТ/СТОП, КОНСОЛЬ, песочница, SAVE/RESTORE
│                           #   (v14.8.16: ЗАПИСЬ ГОЛОСА — enroll профиля voice_id;
│                           #    v14.8.20: источник записи — микрофон ИЛИ звук
│                           #    системы (WASAPI loopback, pyaudiowpatch) — для
│                           #    голоса друга из созвона; структурные команды
│                           #    через manual_commands.json: {"cmd": …})
├── models/                 # nimfea.Q4_K_M.gguf + Modelfile (v14.8.45, активная
│                           #   gemma3+LoRA v7, БЕЗ mmproj) + аварийный откат
│                           #   nimfea-q4_k_m.gguf + Modelfile.nimfea (qwen v6)
├── vita_avatar_app/        # VRM-модельки Нимы + animations/*.vrma
├── data/                   # speech_banwords.txt, donations_inbox.jsonl, backups/
├── cache/                  # manual_commands.json, last_prompt.txt, embeddings.jsonl, ecapa/
└── logs/                   # technical_context.log, pipeline_trace.jsonl
```

## 2. Поток данных

```
микрофон ─┐                        ┌ Twitch-чат (IRC)
loopback ─┤ STT faster-whisper ─────┤ ТЕСТ-ДОНАТ (HTTP :8791 / data/donations_inbox.jsonl)
          │ (общая модель, CPU)     └ debug menu РУЧНОЙ ВВОД (cache/manual_commands.json)
          │
   voice_id: эмбеддинг фразы → имя говорящего (people[].voice_embedding)
          │  mic — всегда владелец; loop — только опознание по порогам
          │  (v14.8.27: новые профили и дозревание на loop запрещены —
          │  профиль собеседника строит ручной enroll в debug menu)
          │
   ears: обращение по имени? ──нет──→ память «подслушанное» + инициатива (конец)
          │ да
   pipeline.DialoguePipeline (worker-поток)
          │  «Нима: …» → прямая озвучка (мимо LLM)
          │  mood = _detect_mood(реплика)
          │  barge-in: «стоп» глушит мгновенно; речь владельца (mic)
          │     перебивает всегда (v14.8.27), loop — только по обращению/
          │     связи с монологом; эхо её собственной речи игнорируется
          ├── prompts.build_system(user_text, mood, speaker):
          │     persona (заземление + [ПОИСК]) + память (гибридный recall,
          │     «сейчас с тобой говорит X») + хвосты нитей + настроение
          ├── llm.generate_stream → резка на предложения НА ЛЕТУ
          │     [ПОИСК: запрос] → web.search → второй ход с результатами
          │     дедуп первых предложений против последних ответов
          ├── tts.speak_stream: синтез N+1 предложения пока играет N → липсинк
          ├── memory.append_dialog(+speaker,+channel) | threads.on_bot_reply
          └── avatar: mood, action, VRMA-анимация, lipsink
```

Перебивание: новая реплика → `tts.stop()`; оба канала STT на паузе, пока
Нимфея говорит. Аватар — через `avatar/avatar_state.json` (fs.watch).

### 2.1. Память v3

- `append_dialog(role, text, user=канал, speaker=имя)` — «когда и что кому
  сказала» в каждой записи; физически ничего не удаляется (только деактивация
  фактов тегом `legacy_junk`).
- **Гибридный recall**: пересечение токенов + 2×косинус эмбеддингов
  (Ollama `nomic-embed-text`, инкрементальный индекс в `cache/embeddings.jsonl`,
  фоновая досборка). Нет эмбеддера — чисто токеновый поиск.
- `memory.query(who, since, until, text)` — произвольные выборки (нити,
  инициатива, будущий экран дебаг-меню).
- Голосовые профили: `people[].voice_embedding`, `set_person_embedding` /
  `update_person_embedding` (скользящее среднее) / `rename_person` /
  `delete_person` (v14.8.21, debug menu) / `next_free_friend_name`.

### 2.2. Нити и инициатива

- `pipeline/threads.py`: хвосты {question|statement} → watchdog: вопрос без
  ответа THREADS_FIRST_SEC → переспрос, ещё THREADS_SECOND_SEC → с претензией;
  максимум 2 подхвата. Переспрос генерит LLM (`pipeline._follow_up`).
- `initiative/`: тишина INITIATIVE_SILENCE_SEC → тема из памяти / отложенный
  веб-факт / случайный вопрос → `pipeline.proactive` (полная цепочка
  LLM→TTS→аватар→память). Любая реплика человека отменяет план.
  **Разгон тем (v14.8.16)**: выбранная тема становится «активной» — пока
  человек молчит, следующие 1-2 инициативы РАЗВИВАЮТ её (другой эпизод
  памяти по той же теме, источник «develop», анти-повтор), а не прыгают на
  случайную. Реплика человека сбрасывает активную тему.

## 3. Окно аватара (avatar/)

- Прозрачное, без рамки, поверх всех, Electron + three.js + @pixiv/three-vrm
  + @pixiv/three-vrm-animation. Пересборка: `cd avatar && npm run build-vrm`.
- **Анимации**: `vita_avatar_app/animations/*.vrma` (7 официальных клипов
  VRoid). Действие `vrma_<имя>` из песочницы играет клип целиком (one-shot,
  заморозка дыхания на время, возврат в A-позу). Карта семантических действий
  `ACTION_VRMA` в `viewer.js` (jump → VRMA_02 и т.п.).
- Управление окном — без изменений (центральная/правая точка, ресайз, зум).

## 4. Бюджет железа (фиксировано, уроки v11)

GTX 1660 Super 6 ГБ VRAM + 16 ГБ RAM. nimfea ~5 ГБ (Ollama, 8k ctx, keep_alive).
TTS — Silero на CPU (RTF ~0.03, стриминг по предложениям). STT faster-whisper
base int8 — CPU, 8 потоков (декод ~1.1 с), имена после STT чинит
_fix_known_names (v14.8.25). CUDA-вариант whisper (~0.7 ГБ) включается явно
NIMA_STT_DEVICE=cuda, когда контекст LLM урезан. voice_id ECAPA — CPU.
Эмбеддинги памяти — Ollama (CPU-priority). Не грузить другие модели в Ollama
(вытеснение → 500).

## 5. Запуск и управление

- **Только через debug menu**: `debug menu.bat` → ЗАПУСК / РЕСТАРТ / ВЫКЛЮЧИТЬ
  (runtime_console: venv-python, env Ollama, без консольного окна, выгрузка VRAM).
- Ручной ввод: ИНСТРУМЕНТЫ → РУЧНОЙ ВВОД (`реплика` / `Нима: реплика` / `!regen`).
- ТРАССА ЗАПРОСА / ТРАССА ОЗВУЧКИ — снова живые (core/pipeline_trace.py →
  logs/pipeline_trace.jsonl).
- ТЕСТ-ДОНАТ — снова живой (twitch/twitch_donations.py: HTTP :8791 или файловый
  ящик; донат благодарится через полный пайплайн).
- БАНВОРДЫ — редактор живой, маскировка вшита в озвучку («мм» вместо слова).

## 6. Правила модульных границ

1. Зависимости: `pipeline → (llm, prompts, memory, stt, tts, avatar, twitch,
   ears, voice_id, web, initiative) → core`. Никаких обратных импортов.
2. Логика — в модулях; `start.py` — только сборка и колбэки.
3. Импорты package-qualified (`from tts.tts_module import TTSModule`).
4. `memory.json` читает/пишет только `memory/`.
5. Характер Нимфеи — только `prompts/persona.py`.
6. Electron-часть аватара меняется только с пересборкой `npm run build-vrm`.
7. Каждое значимое изменение — запись в `docs/CHANGELOG.md`, смена потока
   данных — правка этого файла.
