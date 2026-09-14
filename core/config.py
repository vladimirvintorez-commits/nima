"""Единая конфигурация Нимфеи (v13).

Все пути и настройки собраны здесь. Каждый модуль берёт из config только то,
что ему нужно, и не лезет в чужие модули. Переопределение — через переменные
окружения NIMA_* (debug menu / start.bat могут задавать свои значения).
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# --- пути ---
MEMORY_JSON = PROJECT_ROOT / "memory.json"          # ПАМЯТЬ Нимфеи (не пересоздавать!)
MODELS_DIR = PROJECT_ROOT / "models"                # nimfea-q4_k_m.gguf
AVATAR_DIR = PROJECT_ROOT / "avatar"                # Electron-окно аватара
VRM_DIR = PROJECT_ROOT / "vita_avatar_app"          # VRM-модельки Нимы
CACHE_DIR = PROJECT_ROOT / "cache"
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_PATH = LOGS_DIR / "technical_context.log"

# Голосовые референсы для клонирования (корень проекта)
TTS_REFS = [PROJECT_ROOT / "Референс 1.wav", PROJECT_ROOT / "Референс 2.wav"]

# --- LLM (Ollama + дообученная модель из models/) ---
# Имя создателя: единственный источник правды. Персона, датасет и промпты
# берут его отсюда. Титул «создатель» НЕТРАНСФЕРИРУЕМЫЙ: кем бы ни
# представлялся другой человек, создатель для Нимфы один.
CREATOR_NAME = os.environ.get("NIMA_CREATOR_NAME", "Кизилл")
# v14.3: база gemma3:4b вместо qwen2.5:3b — сообщество сходится, что на 4B
# Gemma заметно лучше в русском и в живом разговорном тоне (меньше цензуры).
# Если модели нет в ollama: `ollama pull gemma3:4b`.
# Когда переобучишь nimfea на куративе (training/README.md), верни:
# Дообученная модель (gemma3:4b + LoRA) зарегистрирована в Ollama как `nimfea`
# (models/README.md — Modelfile + `ollama create`). Дефолт — именно она: базовая
# gemma3:4b без LoRA звучит «ватно» (живой тест 13.09: характер в промпте не
# спасает). Откат на базу: set NIMA_LLM_MODEL=gemma3:4b
LLM_MODEL = os.environ.get("NIMA_LLM_MODEL", "nimfea")
OLLAMA_URL = os.environ.get("NIMA_OLLAMA_URL", "http://127.0.0.1:11434")
LLM_KEEP_ALIVE = os.environ.get("NIMA_LLM_KEEP_ALIVE", "10m")
LLM_TIMEOUT = float(os.environ.get("NIMA_LLM_TIMEOUT", "180"))
LLM_NUM_CTX = int(os.environ.get("NIMA_LLM_NUM_CTX", "8192"))
# Сэмплинг: t=0.85 у 3b на русском уходит в словесный салат (проверено),
# 0.65–0.7 держит связность и характер
LLM_TEMPERATURE = float(os.environ.get("NIMA_LLM_TEMPERATURE", "0.7"))
LLM_TOP_P = float(os.environ.get("NIMA_LLM_TOP_P", "0.85"))
LLM_REPEAT_PENALTY = float(os.environ.get("NIMA_LLM_REPEAT_PENALTY", "1.15"))

# --- STT (faster-whisper, CPU) ---
# Модель: base — декод 1.1 с на 6 с речи (small — 3.5-4.5 с). base коверкает
# имена — их чинит пост-коррекция по известным профилям (pipeline).
# Качество ценой скорости: NIMA_STT_MODEL=small.
# CUDA вариант проверен (декод 1.4 с, ~0.7 ГБ VRAM), но LLM с 8k контекста
# занимает ~5 ГБ — на 6 ГБ карты двоим тесно. Вернуть: NIMA_STT_DEVICE=cuda
# + урезать LLM_NUM_CTX.
STT_MODEL = os.environ.get("NIMA_STT_MODEL", "base")
STT_DEVICE = os.environ.get("NIMA_STT_DEVICE", "cpu")
# Сколько свободной VRAM (МиБ) нужно, чтобы STT взял CUDA (рядом с LLM в Ollama)
STT_VRAM_MIN_MIB = int(os.environ.get("NIMA_STT_VRAM_MIN_MIB", "1800"))
# Микрофонный канал: конкретное устройство ВВОДА по имени/номеру (подстрока,
# регистр не важен). Пусто = микрофон Windows по умолчанию. Нужно, когда по
# умолчанию стоит виртуальный вход (напр. VoiceMeeter Out B1 для созвона) —
# тогда Нима должна слушать физический микрофон напрямую (напр. "DEXP U700").
STT_MIC_DEVICE = os.environ.get("NIMA_STT_MIC_DEVICE", "")
STT_COMPUTE = os.environ.get("NIMA_STT_COMPUTE", "int8")
# Потоки CPU для CTranslate2: дефолт faster-whisper мал; на 12-ядерной машине
# decode 4-6 с на короткой фразе падает примерно вдвое при cpu_threads=8.
STT_CPU_THREADS = int(os.environ.get("NIMA_STT_CPU_THREADS", str(min(8, os.cpu_count() or 4))))
STT_LANGUAGE = os.environ.get("NIMA_STT_LANGUAGE", "ru")
# beam для ЧАСТИЧНОГО скана (триггер «стоп» на лету) — жадный, точность не важна.
STT_BEAM = int(os.environ.get("NIMA_STT_BEAM", "1"))
# beam для ФИНАЛЬНОЙ фразы. История: стоял 5 (точность падежей), но на CPU это
# давало decode 5.8 с на фразу в 1.2 с (трасса 12.09) — разговор не поспевал.
# 2 — компромисс: ~в 2 раза быстрее, точность имён добирает initial_prompt.
STT_BEAM_FINAL = int(os.environ.get("NIMA_STT_BEAM_FINAL", "2"))
STT_INITIAL_PROMPT = os.environ.get(
    "NIMA_STT_PROMPT",
    "Нимфея, Нима, Кизилл, Twitch, Dota, стрим, донат, подписка. Разговор об играх, аниме и стримах.")
# Канальные prompts: микрофону полезны имена/обращения, а loopback общий prompt
# часто навязывал «Нима» чужой разговорной речи. Пустой LOOP prompt отключает
# bias для системного аудио; старое поведение возвращается NIMA_STT_LOOP_PROMPT.
STT_MIC_PROMPT = os.environ.get("NIMA_STT_MIC_PROMPT", STT_INITIAL_PROMPT)
STT_LOOP_PROMPT = os.environ.get("NIMA_STT_LOOP_PROMPT", "")
# Внешняя energy-сегментация уже сохраняет pre-roll и хвост. Внутренний VAD
# Whisper по умолчанию выключен: в свежих логах он целиком удалял 0.9–2.1 с
# уже подтверждённых energy-VAD фраз. Откат: NIMA_STT_WHISPER_VAD=1.
STT_WHISPER_VAD = os.environ.get("NIMA_STT_WHISPER_VAD", "0") == "1"
STT_VAD_MIN_SILENCE_MS = int(os.environ.get("NIMA_STT_VAD_MIN_SILENCE_MS", "300"))
# Короткие однословные фразы допустимы на mic ("стоп", "да"), но loopback
# шумовые обрывки фильтруются отдельно. 0 отключает соответствующий фильтр.
STT_MIN_TEXT_CHARS = int(os.environ.get("NIMA_STT_MIN_TEXT_CHARS", "1"))
STT_LOOP_MIN_TEXT_CHARS = int(os.environ.get("NIMA_STT_LOOP_MIN_TEXT_CHARS", "4"))
STT_LOOP_MIN_WORDS = int(os.environ.get("NIMA_STT_LOOP_MIN_WORDS", "2"))
# Пороги против галлюцинаций Whisper (на CPU/int8 small на шуме/тишине любит
# выдавать «Продолжение следует…», «Спасибо за просмотр», повторы). temperature
# строго 0 — без случайной выборки; log_prob/compression/no_speech фильтруют
# бессмысленные и зацикленные сегменты. Значения — рекомендованные для ru.
STT_NO_SPEECH_THRESHOLD = float(os.environ.get("NIMA_STT_NO_SPEECH", "0.6"))
STT_LOGPROB_THRESHOLD = float(os.environ.get("NIMA_STT_LOGPROB", "-0.8"))
STT_COMPRESSION_THRESHOLD = float(os.environ.get("NIMA_STT_COMPRESSION", "2.2"))
# Фразы-галлюцинации Whisper: если ВСЯ распознанная фраза — одна из них, выкидываем.
# v14.8.50: список расширен по живым логам (титры YouTube, призывы подписаться,
# музыкальные ремарки, англоязычные хвосты). Свой список: NIMA_STT_HALLUCINATIONS.
STT_HALLUCINATION_PHRASES = [p.strip().lower() for p in os.environ.get(
    "NIMA_STT_HALLUCINATIONS",
    "продолжение следует|продолжение в следующей серии|"
    "спасибо за просмотр|спасибо за просмотр видео|спасибо за внимание|"
    "спасибо что смотрели|"
    "субтитры сделал|субтитры создавал|субтитры подготовил|"
    "субтитры делал dimatorzok|редактор субтитров|"
    "редактор субтитров а.семкин корректор а.егорова|корректор а.егорова|"
    "субтитры|dimatorzok|続きは|"
    "подписывайтесь на канал|подписывайтесь|не забудьте подписаться|"
    "ставьте лайки|ставьте лайки и подписывайтесь|"
    "ставьте лайк и подписывайтесь на канал|"
    "играет музыка|звучит музыка|аплодисменты|"
    "thanks for watching|thank you for watching|please subscribe|"
    "like and subscribe").split("|") if p.strip()]
# Фильтр «заезженной пластинки»: whisper на зацикленном шуме повторяет одно и то
# же слово/слог (v14.8.49: «так так так так…», «да-да-да-да-да»). Если в достаточно
# длинной фразе доля уникальных слов слишком мала — это не речь. NIMA_STT_REPEAT=0
# отключает фильтр целиком; пороги настраиваются отдельными переменными.
STT_REPEAT_FILTER = os.environ.get("NIMA_STT_REPEAT", "1") == "1"
STT_REPEAT_MIN_WORDS = int(os.environ.get("NIMA_STT_REPEAT_MIN_WORDS", "6"))
STT_REPEAT_MAX_UNIQUE_RATIO = float(os.environ.get("NIMA_STT_REPEAT_RATIO", "0.34"))
# Сегментация речи: энергия + тишина
STT_SAMPLE_RATE = 16000
STT_SILENCE_CUT = float(os.environ.get("NIMA_STT_SILENCE_CUT", "0.5"))   # сек тишины = конец фразы
STT_MIN_SPEECH = float(os.environ.get("NIMA_STT_MIN_SPEECH", "0.35"))    # мин. длина речи, сек
STT_RMS_THRESHOLD = float(os.environ.get("NIMA_STT_RMS_THRESHOLD", "0.004"))
# Программное усиление микрофона ДО VAD/whisper (только канал mic). Живой
# замер v14.8.2: DEXP U700 очень тихий — фон ~0.0009, речь в пике ~0.0026 при
# пороге 0.004 → VAD почти не срабатывал («Нима глухая»). Усиление x3 поднимает
# речь над порогом, не трогая Windows-настройки (для созвона они настроены).
STT_MIC_GAIN = float(os.environ.get("NIMA_STT_MIC_GAIN", "1.0"))
# Адаптивный порог = шумовой_фон * X. Стандартные 3.0 при соотношении
# речь/фон ~3 у тихого микрофона съедали саму речь — у Нимфеи 2.0.
STT_RMS_NOISE_MULT = float(os.environ.get("NIMA_STT_RMS_NOISE_MULT", "3.0"))
STT_MAX_UTTERANCE = float(os.environ.get("NIMA_STT_MAX_UTTERANCE", "30"))  # принудительная нарезка

# --- Перебивание (barge-in): микрофон слушает и ПОКА НИМА ГОВОРИТ ---
# Жёсткие триггеры: сразу глушат речь (сравнение по подстроке, без имени)
BARGE_TRIGGERS = [w.strip() for w in os.environ.get(
    "NIMA_BARGE_TRIGGERS",
    "замолчи,молчать,заткнись,стоп,тихо,хватит,подожди,погоди,стоп-стоп").split(",") if w.strip()]
# Адаптивное перебивание: фраза связана с монологом (есть общие значимые слова),
# но НЕ является эхом её собственной речи (почти дословное совпадение — игнор)
BARGE_MIN_SHARED = int(os.environ.get("NIMA_BARGE_SHARED", "1"))
BARGE_ECHO_OVERLAP = float(os.environ.get("NIMA_BARGE_ECHO", "0.85"))
BARGE_MIN_WORDS = int(os.environ.get("NIMA_BARGE_MIN_WORDS", "3"))
BARGE_WINDOW_SEC = float(os.environ.get("NIMA_BARGE_WINDOW", "10"))  # после «Нима!»/стопа — ответ без имени
STT_BARGE_RMS_MULT = float(os.environ.get("NIMA_STT_BARGE_RMS", "1.8"))  # порог мика, пока говорит (эхо)
STT_PARTIAL_SEC = float(os.environ.get("NIMA_STT_PARTIAL", "0.9"))  # скан триггеров по недоговорённой фразе
# Эхо-фильтр ОТЛОЖЕННОГО loopback: доля слов фразы, совпадающих с её последним
# монологом, при которой фраза считается её собственным эхом (её TTS из
# динамиков). 0.6 съедал ответы друга ПО ТЕМЕ монолога (в живых логах overlap
# 0.67–0.88: «Падал тоже, ты его поправила») — реплика молча выбрасывалась без
# субтитра и без памяти. Субтитры друга «не пишутся» — вот причина. Теперь
# отсекается только почти дословный повтор (её собственный голос).
ECHO_LOOP_OVERLAP = float(os.environ.get("NIMA_LOOP_ECHO", "0.9"))

# --- TTS (XTTS v2, клонирование голоса по референсам) ---
TTS_LANGUAGE = os.environ.get("NIMA_TTS_LANGUAGE", "ru")
# Движок озвучки — v14.3 основной: silero (мгновенный CPU, RTF ~0.05, чистый
# русский, НО фиксированные голоса — клона нет). Замеры бюджета клона при
# живой gemma3:4b (карта 6 ГБ, свободно ~2 ГБ): XTTS +1.7 ГБ / RTF ~0.55-1.0;
# Chatterbox +2.1 ГБ / RTF ~1.4; оба ВПРИТЫК (запас <300 МиБ), F5-TTS на этой
# машине сломан (RTF 9-18, переголовка фреймворка). Клон включается вручную:
#   set NIMA_TTS_BACKEND=xtts
# Если движок не установлен, tts_module сам откатывается на xtts.
TTS_BACKEND = os.environ.get("NIMA_TTS_BACKEND", "silero")
# Голос Silero (клона нет — берём из фиксированного набора v5):
# xenia / baya / kseniya (женские), aidar / eugene (мужские), random
# (baya выбрана прослушкой v14.3.2)
SILERO_SPEAKER = os.environ.get("NIMA_TTS_SPEAKER", "baya")
# Глобальный сдвиг высоты тона (SSML-prosody pitch): «+25%» — «пикми-тян»,
# выбран прослушкой лесенки +10/+18/+25/+35 (v14.3.4). 0/пусто — как есть.
# Суммируется с эмоциональным pitch; потолок итога +35% (выше «плывёт»).
# Референс-сэмплы: cache/baya_picky_*.wav, q25_*.wav, e25_*.wav.
SILERO_PITCH_SHIFT = os.environ.get("NIMA_TTS_PITCH", "+25%")
# Версия модели Silero (torch.hub тянет master, там уже есть v5-поколение):
# v5_5_ru — новейшая, чище звук; фолбэк на v4_ru встроен. Голоса те же
# (xenia/baya/kseniya/aidar/eugene), API и SSML-эмоции совместимы.
SILERO_VERSION = os.environ.get("NIMA_TTS_VERSION", "v5_5_ru")
# Частота синтеза Silero: модель умеет 8000/24000/48000. 24 кГц слышно
# «металлом» на согласных; родные 48 кГц мягче, RTF всё ещё ~0.1-0.4.
SILERO_SAMPLE_RATE = int(os.environ.get("NIMA_TTS_SAMPLE_RATE", "48000"))
TTS_DEVICE = os.environ.get("NIMA_TTS_DEVICE", "")   # "" = авто (cuda → cpu)
TTS_MAX_CHARS = int(os.environ.get("NIMA_TTS_MAX_CHARS", "400"))
# Озвучка чанками, а не предложениями: короткие предложения склеиваются
# (проза не «прыгает» между синтезами — меньше роботизированности), слишком
# длинный первый чанк режется по запятой ради раннего первого звука
TTS_FIRST_MIN_CHARS = int(os.environ.get("NIMA_TTS_FIRST_MIN", "25"))
TTS_MERGE_CHARS = int(os.environ.get("NIMA_TTS_MERGE", "200"))
TTS_SPLIT_AT = int(os.environ.get("NIMA_TTS_SPLIT_AT", "220"))
# Параметры сэмплинга XTTS (дефолты библиотеки дают суховато-роботичный тон)
TTS_TEMPERATURE = float(os.environ.get("NIMA_TTS_TEMPERATURE", "0.7"))
TTS_TOP_P = float(os.environ.get("NIMA_TTS_TOP_P", "0.9"))
TTS_REPETITION_PENALTY = float(os.environ.get("NIMA_TTS_REPETITION", "1.8"))
# Параметры Chatterbox: exaggeration — эмоциональная окраска клона (0..1),
# cfg_weight — насколько строго держаться референса (меньше = выразительнее)
CHATTERBOX_TEMPERATURE = float(os.environ.get("NIMA_CB_TEMPERATURE", "0.8"))
CHATTERBOX_EXAGGERATION = float(os.environ.get("NIMA_CB_EXAGGERATION", "0.5"))
CHATTERBOX_CFG_WEIGHT = float(os.environ.get("NIMA_CB_CFG", "0.4"))
# Эмоция → exaggeration Chatterbox (когда эмоция задана — берётся она, не база)
EMOTION_EXAGGERATION = {
    "normal": None, "joy": 0.6, "excitement": 0.75, "interest": 0.5,
    "thinking": 0.3, "sadness": 0.3, "anger": 0.7, "fear": 0.65,
    "shyness": 0.4, "arousal": 0.8, "indifference": 0.3,
    "boredom": 0.25, "sleeping": 0.2,
}
# Эмоция → скорость речи (множитель duration); живость голоса от настроения
EMOTION_SPEED = {
    "normal": 1.0, "joy": 1.08, "excitement": 1.15, "interest": 1.02,
    "thinking": 0.95, "sadness": 0.9, "anger": 1.12, "fear": 1.1,
    "shyness": 0.93, "arousal": 0.92, "indifference": 0.98,
    "boredom": 0.9, "sleeping": 0.85,
}
# Куда играет TTS. "" = устройство по умолчанию. СЦЕНАРИЙ «НАУШНИКИ»:
# собеседники в комнате/созвоне НЕ слышат Ниму (звук в наушниках, микрофон
# их не слышит). Решение — виртуальный кабель VB-Audio Cable (бесплатно):
#   1) ставишь VB-Cable → появляется устройство воспроизведения «CABLE Input»;
#   2) NIMA_TTS_OUTPUT_DEVICE="CABLE Input"      → её голос уходит в кабель;
#   3) NIMA_TTS_MONITOR_DEVICE="наушники"        → дублируют тебе в уши;
#   4) в созвоне выбираешь микрофоном «CABLE Output» → друзья слышат Ниму.
# Значение — часть имени устройства (регистр не важен) или номер.
TTS_OUTPUT_DEVICE = os.environ.get("NIMA_TTS_OUTPUT_DEVICE", "")
TTS_MONITOR_DEVICE = os.environ.get("NIMA_TTS_MONITOR_DEVICE", "")

# Silero: эмоция → SSML-prosody. Rate — ТОЛЬКО словесные значения
# ('slow' ≈ +22% времени, 'fast' ≈ −18%): проценты в rate у v4_ru сломаны
# («+8%» растягивает аудио в 12 раз). Pitch — проценты от базового тона,
# работают корректно: радость/азарт выше, грусть/скука/сон ниже.
SILERO_EMOTION_RATE = {
    "normal": None, "joy": None, "excitement": "fast", "interest": None,
    "thinking": "slow", "sadness": "slow", "anger": "fast", "fear": "fast",
    "shyness": "slow", "arousal": None, "indifference": None,
    "boredom": "slow", "sleeping": "slow",
}
SILERO_EMOTION_PITCH = {
    # Добавки подобраны так, чтобы ИТОГ с базой +25% не выходил за +35%:
    # выше Silero v5 «плывёт» (проверено: joy 25+12=+37 — артефакты, v14.3.5)
    "normal": None, "joy": "+8%", "excitement": "+10%", "interest": "+6%",
    "thinking": "-4%", "sadness": "-12%", "anger": "+8%", "fear": "+10%",
    "shyness": "+7%", "arousal": "+8%", "indifference": "-3%",
    "boredom": "-10%", "sleeping": "-14%",
}

# --- Аватар ---
AVATAR_STATE_PATH = AVATAR_DIR / "avatar_state.json"
AVATAR_LOCKED_DEFAULT = os.environ.get("NIMA_AVATAR_LOCKED", "0") == "1"
AVATAR_ALWAYS_ON_TOP = os.environ.get("NIMA_AVATAR_ALWAYS_ON_TOP", "1") == "1"
LAST_OUTFIT_PATH = CACHE_DIR / "last_outfit.txt"   # одежда переживает перезапуск
# Своя инициатива в одежде: шанс после каждого ответа + не чаще раза за кулдаун
OUTFIT_SELF_CHANCE = float(os.environ.get("NIMA_OUTFIT_SELF_CHANCE", "0.04"))
OUTFIT_SELF_COOLDOWN_SEC = int(os.environ.get("NIMA_OUTFIT_SELF_COOLDOWN", "2400"))

# --- Ручной ввод (debug menu) ---
MANUAL_QUEUE_PATH = CACHE_DIR / "manual_commands.json"
MANUAL_LAST_PATH = CACHE_DIR / "last_manual.txt"
LAST_PROMPT_PATH = CACHE_DIR / "last_prompt.txt"
ENROLL_RESULT_PATH = CACHE_DIR / "enroll_result.json"   # результат записи голоса (debug menu)

# --- Twitch (необязательно) ---
TWITCH_CHANNEL = os.environ.get("NIMA_TWITCH_CHANNEL", "")
TWITCH_TOKEN = os.environ.get("NIMA_TWITCH_TOKEN", "")
TWITCH_NICK = os.environ.get("NIMA_TWITCH_NICK", "nimfea_bot")
# Режим стрима для персоны: NIMA_STREAM_MODE=1/0 перекрывает; по умолчанию
# «стрим идёт» считается, если задан канал Twitch. Вне стрима Нимфея не
# упоминает чат/зрителей/донаты (иначе она несёт про эфир на пустом канале).
STREAM_ACTIVE = os.environ.get(
    "NIMA_STREAM_MODE", "1" if os.environ.get("NIMA_TWITCH_CHANNEL") else "0") == "1"

# --- Донаты (debug menu → ТЕСТ-ДОНАТ, вебхуки моста) ---
DONATIONS_INBOX_PATH = PROJECT_ROOT / "data" / "donations_inbox.jsonl"
DONATIONS_HTTP_PORT = int(os.environ.get("NIMA_DONATIONS_PORT", "8791"))

# --- Промпт: компактная персона (v14.7.6, латентность) ---
# Характер Нимы уже вшит в веса дообученной модели (gemma3:4b + LoRA v2), поэтому
# полную персону (~1500 токенов) держать в промпте не нужно — она только удлиняет
# prompt-eval на каждый ответ. При PERSONA_COMPACT=1 (дефолт) в system идёт
# компактная персона (только протоколы/создатель/запрет ассистента/теги). Откат
# на полную персону (нужна для НЕдообученной базовой gemma3): NIMA_PERSONA_COMPACT=0.
PERSONA_COMPACT = os.environ.get("NIMA_PERSONA_COMPACT", "1") == "1"

# --- Память: сколько последних реплик уходит в промпт ---
# Лимиты снижены (v14.7.6) ради латентности: меньше токенов в system → короче
# prompt-eval. Факты в промпт идут релевантным топом (recall), а не «всё подряд».
MEMORY_HISTORY_LIMIT = int(os.environ.get("NIMA_MEMORY_HISTORY", "8"))
MEMORY_FACTS_LIMIT = int(os.environ.get("NIMA_MEMORY_FACTS", "6"))
MEMORY_RECALL_FACTS = int(os.environ.get("NIMA_MEMORY_RECALL_FACTS", "4"))
MEMORY_RECALL_DIALOG = int(os.environ.get("NIMA_MEMORY_RECALL_DIALOG", "2"))
# Quality gates are deliberately cheap and reversible: lexical hits always pass;
# embedding-only matches need a meaningful cosine instead of any positive value.
MEMORY_RECALL_MIN_COSINE = float(os.environ.get("NIMA_MEMORY_RECALL_MIN_COSINE", "0.42"))
MEMORY_CONTEXT_MAX_CHARS = int(os.environ.get("NIMA_MEMORY_CONTEXT_MAX_CHARS", "1800"))
MEMORY_HISTORY_SPEAKER = os.environ.get("NIMA_MEMORY_HISTORY_SPEAKER", "1") == "1"
MEMORY_INCLUDE_ALL_PEOPLE = os.environ.get("NIMA_MEMORY_INCLUDE_ALL_PEOPLE", "0") == "1"

# Lightweight response cleanup. It never regenerates and therefore adds no
# model latency; set NIMA_RESPONSE_CLEANUP=0 for exact legacy output.
RESPONSE_CLEANUP = os.environ.get("NIMA_RESPONSE_CLEANUP", "1") == "1"
RESPONSE_MAX_SENTENCES = int(os.environ.get("NIMA_RESPONSE_MAX_SENTENCES", "3"))

# --- Банворды озвучки (debug menu → БАНВОРДЫ) ---
SPEECH_BANWORDS_PATH = PROJECT_ROOT / "data" / "speech_banwords.txt"

# --- Уши: второй канал (loopback — весь звук системы, напр. созвон) ---
# 0 = только микрофон; 1 = микрофон + WASAPI loopback от устройства вывода
STT_LOOPBACK = os.environ.get("NIMA_STT_LOOPBACK", "1") == "1"
STT_LOOPBACK_DEVICE = os.environ.get("NIMA_STT_LOOPBACK_DEVICE", "")  # имя/номер вывода, "" = по умолчанию
STT_LOOPBACK_RMS = float(os.environ.get("NIMA_STT_LOOPBACK_RMS", "0.02"))  # у системного звука выше порог
# Отдельная (вторая) модель whisper для loopback: звуки игры/компа распознаются
# в своём потоке и НЕ заставляют микрофонный канал ждать в очереди (урок v13:
# с общей моделью STT «жёстко тупил» из-за звуков с компа).
STT_LOOPBACK_SHARED = os.environ.get("NIMA_STT_LOOPBACK_SHARED", "0") == "1"

# --- Уши: гейт адресации (слышит всегда, отвечает по имени) ---
EARS_ENABLED = os.environ.get("NIMA_EARS_ENABLED", "1") == "1"
EARS_NAMES = [n.strip() for n in os.environ.get(
    "NIMA_EARS_NAMES", "нимфея,нима,ним,нимф,нимуль,нимочка,nimfea,nima").split(",") if n.strip()]
EARS_CLASSIFIER = os.environ.get("NIMA_EARS_CLASSIFIER", "1") == "1"  # резервный микро-классификатор LLM
EARS_OVERHEAR_TO_MEMORY = os.environ.get("NIMA_EARS_OVERHEAR", "1") == "1"
# Маршрутизация адресата (v14.4.1): 'smart' — реплики делятся на «ей / Кизиллу /
# третьему / никому» (словарь → контекст → LLM-классификация); 'strict' — как
# раньше: отвечает на любую реплику со своим именем, остальное в подслышанное.
EARS_MODE = os.environ.get("NIMA_EARS_MODE", "smart")
# Окно диалога: сколько секунд после её реплики конкретному человеку его
# следующий ход не требует её имени («Вася: а ты?» — понятно, кому)
EARS_DIALOG_WINDOW_SEC = float(os.environ.get("NIMA_EARS_DIALOG_WINDOW", "12"))
# LLM-классификация адресата для неоднозначных вопросительных реплик (слой 3)
EARS_LLM_ADDRESSEE = os.environ.get("NIMA_EARS_LLM_ADDR", "1") == "1"

# --- voice_id: кто говорит (speechbrain ECAPA, CPU; без пакета — по каналам) ---
VOICE_ID_ENABLED = os.environ.get("NIMA_VOICE_ID", "1") == "1"
VOICE_ID_THRESHOLD = float(os.environ.get("NIMA_VOICE_ID_THRESHOLD", "0.72"))  # косинус схожести
# мягкая граница: ниже точного порога, но всё ещё «похоже на профиль» —
# прикрепляем фразу к лучшему профилю вместо завода нового «Друга N».
# 0.45 (было 0.50): живой тест 13.09 — друг с профилем стабильно давал 0.46
# (сжатие созвона) и каждый раз плодил «Друга N»; межпрофильная схожесть
# разных людей ~0.2-0.35, так что 0.45 всё ещё не путает людей.
VOICE_ID_SOFT_THRESHOLD = float(os.environ.get("NIMA_VOICE_ID_SOFT", "0.45"))
# короче этого (сек) незнакомый голос профиль не создаёт (обрывки/шум)
VOICE_ID_MIN_NEW_SEC = float(os.environ.get("NIMA_VOICE_ID_MIN_NEW_SEC", "1.5"))
VOICE_ID_USER = os.environ.get("NIMA_VOICE_ID_USER", "Кизилл")  # имя владельца микрофона

# --- web: интернет-поиск ---
WEB_ENABLED = os.environ.get("NIMA_WEB_SEARCH", "1") == "1"
WEB_RESULTS = int(os.environ.get("NIMA_WEB_RESULTS", "5"))
WEB_UNANSWERED_SEC = float(os.environ.get("NIMA_WEB_UNANSWERED_SEC", "180"))  # спросила — ответа нет → ищет сама

# --- initiative: живая инициатива (реплики без обращения) ---
INITIATIVE_ENABLED = os.environ.get("NIMA_INITIATIVE", "1") == "1"
# Активный профиль ВСЕГДА (и вне стрима тоже): она живой человек, а не ждёт
# 4 минуты тишины. Старое консервативное поведение возвращается явными
# NIMA_INITIATIVE_SILENCE=240 и NIMA_INITIATIVE_GAP=300.
INITIATIVE_STREAM_PROFILE = os.environ.get(
    "NIMA_INITIATIVE_STREAM_PROFILE", "1" if STREAM_ACTIVE else "0") == "1"
_INITIATIVE_DEFAULT_SILENCE = "20"
_INITIATIVE_DEFAULT_GAP = "35"
INITIATIVE_SILENCE_SEC = float(os.environ.get(
    "NIMA_INITIATIVE_SILENCE", _INITIATIVE_DEFAULT_SILENCE))
INITIATIVE_MIN_GAP_SEC = float(os.environ.get(
    "NIMA_INITIATIVE_GAP", _INITIATIVE_DEFAULT_GAP))
# Чат живее loopback: валидное сообщение ненадолго откладывает инициативу, но
# поток сообщений не должен навсегда сбрасывать основной таймер пользователя.
INITIATIVE_CHAT_QUIET_SEC = float(os.environ.get("NIMA_INITIATIVE_CHAT_QUIET", "12"))
# После обычного ответа Нимы даём людям договорить, но не запускаем полный
# proactive cooldown. Между инициативами по-прежнему действует GAP.
INITIATIVE_REPLY_QUIET_SEC = float(os.environ.get("NIMA_INITIATIVE_REPLY_QUIET", "8"))
INITIATIVE_POLL_SEC = float(os.environ.get("NIMA_INITIATIVE_POLL", "2"))
# Защита от гонки: активность в этом окне между eligibility и callback отменяет
# уже подготовленную инициативу. 0 отключает дополнительный guard.
INITIATIVE_INTERRUPT_GUARD_SEC = float(os.environ.get(
    "NIMA_INITIATIVE_INTERRUPT_GUARD", "1.5"))
# Синхронный web search в момент срабатывания создавал многосекундную задержку.
# По умолчанию используются только заранее накопленные web-факты; откат = 1.
INITIATIVE_LIVE_WEB = os.environ.get("NIMA_INITIATIVE_LIVE_WEB", "0") == "1"
# Кулдаун на УЖЕ озвученную тему инициативы (жёстче — дольше не повторяется).
# Урок живого теста 2026-09-11: ночью пул из 8 вопросов крутился по кругу —
# «часами говорит одно и то же». Теперь тема с перекрытием слов ≥0.6 гасится.
INITIATIVE_TOPIC_COOLDOWN_SEC = float(os.environ.get(
    "NIMA_INITIATIVE_TOPIC_COOLDOWN", "7200"))

# Анимация «приветствия» (greeting) не чаще раза в этот интервал. Урок живого
# прогона 11.09: машала каждые 15–30 с — срабатывало на СВОЙ голос с loopback
# (эхо её же «привет») и на любое «привет» внутри длинной фразы.
GREETING_ANIM_COOLDOWN_SEC = float(os.environ.get(
    "NIMA_GREETING_ANIM_COOLDOWN", "90"))

# --- нити разговора (переспросы/претензии) ---
THREADS_ENABLED = os.environ.get("NIMA_THREADS", "1") == "1"
THREADS_FIRST_SEC = float(os.environ.get("NIMA_THREADS_FIRST", "8"))    # до первого хвоста
THREADS_SECOND_SEC = float(os.environ.get("NIMA_THREADS_SECOND", "16")) # до второго (с претензией)

# --- зрение: она видит экран (gemma3:4b мультимодальная — ТОТ ЖЕ Ollama,
# второй VLM не заводим: на 6 ГБ своп моделей = десятки секунд тишины) ---
VISION_ENABLED = os.environ.get("NIMA_VISION", "1") == "1"
# фоновое наблюдение: раз в N секунд глянуть и запомнить (она НЕ говорит —
# только память/контекст; вслух увиденное уходит через фоновые комментарии)
VISION_INTERVAL_SEC = float(os.environ.get("NIMA_VISION_INTERVAL", "20"))
# режим просмотра (тишина в комнате + кадры меняются = видео/игра): смотрит чаще
VISION_WATCH_INTERVAL_SEC = float(os.environ.get("NIMA_VISION_WATCH_INTERVAL", "12"))
# сколько секунд тишины + движение на экране → режим просмотра
VISION_WATCH_QUIET_SEC = float(os.environ.get("NIMA_VISION_WATCH_QUIET", "120"))
# захват экрана: ширина скриншота (gemma3 ест ~896px тайлы) и качество JPEG
VISION_MAX_WIDTH = int(os.environ.get("NIMA_VISION_WIDTH", "896"))
VISION_JPEG_QUALITY = int(os.environ.get("NIMA_VISION_JPEG", "72"))
# наблюдение уходит в постоянную память (факт, попадает в RAG) не чаще этого
VISION_FACT_MIN_SEC = float(os.environ.get("NIMA_VISION_FACT_MIN", "90"))
# сколько последних наблюдений уходит в промпт (контекстная память увиденного)
VISION_RECENT_N = int(os.environ.get("NIMA_VISION_RECENT", "5"))
# Краткая запись-ссылка в промпт (v14.7.6, латентность): в system идёт КОРОТКАЯ
# метка наблюдения (первое предложение, до VISION_BRIEF_CHARS символов), а не
# полный абзац описания кадра. Полное описание НЕ теряется — оно как и раньше
# уходит в постоянную память фактом «Видела на экране: …» (RAG найдёт по запросу)
# и доступно свежим взглядом describe_now, когда спрашивают про экран.
# NIMA_VISION_BRIEF=0 возвращает полные наблюдения в промпт.
VISION_BRIEF = os.environ.get("NIMA_VISION_BRIEF", "1") == "1"
VISION_BRIEF_CHARS = int(os.environ.get("NIMA_VISION_BRIEF_CHARS", "90"))
# минимум тишины от последней реплики человека, чтобы фон начал глядеть
# (пока она говорит/генерирует, busy_check и так откладывает взгляд — здесь
# только крошечная пауза после реплики; она живой человек и реагирует сразу)
VISION_MIN_QUIET_SEC = float(os.environ.get("NIMA_VISION_MIN_QUIET", "10"))
# активное окно: заголовок foreground-окна (ctypes, Windows) уходит в запрос
# описания кадра — она знает, ВО ЧТО играет/смотрит, а не просто «экран»
VISION_WINDOW_TITLE = os.environ.get("NIMA_VISION_WINDOW", "1") == "1"
VISION_WINDOW_TITLE_MAX = int(os.environ.get("NIMA_VISION_WINDOW_MAX", "90"))
# Отдельное расписание фоновых комментариев. Откат одним флагом:
# NIMA_VISION_COMMENTS=0 оставляет захват, describe_now и память как раньше.
# Значения АКТИВНЫЕ всегда (живой человек и вне стрима): заметила → через 5 с
# говорит, между комментариями 35 с. Откат: NIMA_VISION_COMMENT_DELAY/COOLDOWN.
VISION_COMMENTS_ENABLED = os.environ.get("NIMA_VISION_COMMENTS", "1") == "1"
VISION_COMMENT_DELAY_SEC = float(os.environ.get("NIMA_VISION_COMMENT_DELAY", "5"))
VISION_COMMENT_COOLDOWN_SEC = float(os.environ.get("NIMA_VISION_COMMENT_COOLDOWN", "35"))
# TTL кандидата-комментария должен пережить и задержку, и полный cooldown,
# иначе валидный комментарий гарантированно протухает раньше, чем сможет
# пройти гейт cooldown (был баг «видит, но молчит»). Держим запас +15 с.
VISION_OBSERVATION_TTL_SEC = float(os.environ.get(
    "NIMA_VISION_OBSERVATION_TTL",
    str(max(120.0, VISION_COMMENT_DELAY_SEC + VISION_COMMENT_COOLDOWN_SEC + 15.0))))
VISION_NOVELTY_THRESHOLD = float(os.environ.get("NIMA_VISION_NOVELTY", "0.45"))
VISION_SCENE_CHANGE_THRESHOLD = float(os.environ.get("NIMA_VISION_SCENE_CHANGE", "6.0"))
VISION_COMMENT_INTERRUPT_GUARD_SEC = float(os.environ.get(
    "NIMA_VISION_COMMENT_GUARD", "1.5"))
# Доля инициатив, которые позволяется перехватить vision-теме. 0.55 делало
# «глаза навязчивыми»: больше половины живых реплик было про экран (в логах
# «Что там написано в окне ZCode?» — 12 раз за 10 минут).
VISION_INITIATIVE_CHANCE = float(os.environ.get("NIMA_VISION_INITIATIVE", "0.2"))
# Один и тот же недогляд («ВОПРОС: …») не переспрашиваем чаще этого интервала.
VISION_QUESTION_COOLDOWN_SEC = float(os.environ.get(
    "NIMA_VISION_QUESTION_COOLDOWN", "300"))

# --- watchdog: сторожевой пёс над живыми потоками (v14.7) ---
# STT-потоки и Ollama умирают тихо (loopback дважды умирал в живых тестах,
# это ловилось только человеком) — watchdog проверяет и реанимирует сам.
WATCHDOG_ENABLED = os.environ.get("NIMA_WATCHDOG", "1") == "1"
WATCHDOG_INTERVAL_SEC = float(os.environ.get("NIMA_WATCHDOG_INTERVAL", "15"))
WATCHDOG_OLLAMA_INTERVAL_SEC = float(os.environ.get("NIMA_WATCHDOG_OLLAMA", "60"))

# --- дневник дня (v14.7): ночью сжимает день в воспоминание (факт) ---
DIARY_ENABLED = os.environ.get("NIMA_DIARY", "1") == "1"
DIARY_HOUR = int(os.environ.get("NIMA_DIARY_HOUR", "3"))          # после 03:00
DIARY_CHECK_SEC = float(os.environ.get("NIMA_DIARY_CHECK", "600"))  # период проверки
DIARY_LAST_PATH = CACHE_DIR / "diary_last.txt"
DIARY_MAX_EPISODES = int(os.environ.get("NIMA_DIARY_EPISODES", "40"))

# --- глобальные хоткеи (v14.7, только Windows) ---
HOTKEYS_ENABLED = os.environ.get("NIMA_HOTKEYS", "1") == "1"


def ensure_dirs() -> None:
    for d in (CACHE_DIR, LOGS_DIR, AVATAR_DIR):
        d.mkdir(parents=True, exist_ok=True)
