import re
from pathlib import Path

# Корень проекта = родитель debug_menu/: меню работает при переносе проекта,
# без захардкоженного диска/пути.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_DESTINATION = Path("B:/")
CHANGELOG_PATH = PROJECT_ROOT / "docs" / "CHANGELOG.md"
LOG_PATH = PROJECT_ROOT / "logs" / "technical_context.log"

# Реестр снимков проекта для частичных (инкрементальных) сохранений: при каждом
# сохранении меню запоминает {путь: [mtime, size]} вместе с версией сохранения.
# Живёт внутри debug_menu/ — переживает полное восстановление проекта.
SNAPSHOTS_PATH = PROJECT_ROOT / "debug_menu" / "snapshots.json"

# Частичное сохранение: метка в имени архива (Нимфея V1.2.3 INC описание.rar)
# и имя файла-манифеста внутри архива, рядом с корневой папкой проекта.
INC_TAG = " INC"
INC_MANIFEST_NAME = "_incremental_manifest.json"

# Файлы/папки дебаг-меню — никогда не удаляются и не перезаписываются при restore
# (v12: меню консолидировано в debug_menu/ — лаунчеры больше не в tools/ и корне)
DEBUG_FILES = {
    "debug_menu",
    "debug menu.bat",
}

# v12 «чистое поле»: рантайм-пакеты (ai/app/core/dialogue/voice/…) снесены,
# собираются заново. Список модулей оставлен как эталон того, ЧТО меню
# проверяло в v11 — при отстройке новых механик возвращай модули сюда.
PROJECT_MODULES = [
    ("core.config", "PROJECT_ROOT"),
    ("core.pipeline_trace", "mark"),
    ("ears.ears_module", "is_addressed"),
    ("llm.llm_module", "LLMModule"),
    ("memory.memory_module", "MemoryModule"),
    ("pipeline.pipeline", "DialoguePipeline"),
    ("pipeline.outfits", "detect_outfit_request"),
    ("pipeline.threads", "ConversationThreads"),
    ("prompts.prompt_builder", "PromptBuilder"),
    ("prompts.persona", "PERSONA"),
    ("stt.stt_module", "STTModule"),
    ("tts.tts_module", "TTSModule"),
    ("web.web_module", "search"),
    ("initiative.initiative_module", "InitiativeModule"),
    ("voice_id.voice_id_module", "VoiceID"),
    ("twitch.twitch_donations", "start_http_receiver"),
    ("avatar.bridge", "AvatarBridge"),
]

EXCLUDED_ALWAYS = {".postman", "postman"}
EXCLUDED_COMMON = {"__pycache__"}
TEMP_SUFFIXES = {".pyc", ".pyo", ".tmp", ".temp", ".log"}

# VRM-модельки Нимы лежат в корне vita_avatar_app; состояние окна — avatar/
VITA_MODEL_DIR = PROJECT_ROOT / "vita_avatar_app"
VITA_VRM_PATH = PROJECT_ROOT / "vita_avatar_app" / "Nima_standart.vrm"
AVATAR_STATE_PATH = PROJECT_ROOT / "avatar" / "avatar_state.json"

BG = "#0a0a0a"
# Служебный цвет прозрачности главного меню: нигде не рисуется, кроме пустого
# фона canvas главного экрана, и объявляется через -transparentcolor на Windows —
# сквозь него видно рабочий стол. Не должен совпадать с BG/DIM.
TRANSPARENT = "#0d0e11"
FG = "#33ff33"
AMBER = "#ffb000"
DIM = "#1a4a1a"
RED_ERR = "#ff4444"
FONT = ("Courier New", 11)
FONT_SM = ("Courier New", 9)
FONT_LG = ("Courier New", 13, "bold")

def get_version():
    try:
        text = CHANGELOG_PATH.read_text(encoding="utf-8", errors="ignore")
        # v13+: заголовок версии «Нимфея vX.Y.Z»; исторические записи — «Vita vX.Y.Z»
        match = re.search(r"(?:Нимфе[яи]|Vita)\s+v?(\d+\.\d+\.\d+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "?.?.?"


def version_from_name(name):
    """Версия из имени бэкапа («Нимфея V11.4.0 INC …» → '11.4.0') или None."""
    match = re.search(r"[Vv]\s?(\d+\.\d+\.\d+)", name or "")
    return match.group(1) if match else None

ANIMATIONS = [
    # Только то, что реально играет в viewer.js (клин-карта ACTION_VRMA +
    # процедурные жесты proc_*). Мёртвые пункты (looking/listening/thinking/
    # speaking/sleeping/boredom_*/breast_shake_*) удалены — за них ни один
    # код не цеплялся, кнопки выглядели «сломанными».
    ("idle", "Ожидание (остановить анимацию)"),
    ("jump", "Прыжок (VRMA_02)"),
    ("spinning", "Кружится (VRMA_04)"),
    ("greeting", "Приветствие (VRMA_03)"),
    ("dance", "Танец (VRMA_01)"),
    ("wave", "Машет рукой (Goodbye)"),
    ("stretch", "Потягивается (VRMA_07)"),
    # Процедурные жесты idle — только те, что остались в живом пуле (v14.8.15).
    ("proc_ears", "Жест: дёргает ушками"),
    ("proc_tail", "Жест: виляет хвостом"),
]

# VRMA-анимации (v11.0.1): всё, что лежит в vita_avatar_app/animations/*.vrma,
# автоматически появляется в песочнице как действие 'vrma_<ИмяФайла>'.
# Новые файлы из свободного доступа достаточно положить в папку.
def _load_vrma_animations():
    result = []
    vrma_dir = PROJECT_ROOT / "vita_avatar_app" / "animations"
    try:
        for path in sorted(vrma_dir.glob("*.vrma")):
            stem = path.stem
            if not stem or stem.startswith("_"):
                continue
            result.append((f"vrma_{stem}", f"VRMA: {stem}"))
    except OSError:
        pass
    return result

ANIMATIONS.extend(_load_vrma_animations())

# Эмоции (mood), которые применяются через animation_integration.command_avatar(mood=...).
# Источник — MOOD_STYLE_GUIDE в speech_style.py (+ boredom и normal). Ручной выбор
# в debug menu помогает проверить, что настроение корректно отражается на аватаре.
EMOTIONS = [
    ("normal", "Нейтральное"),
    ("joy", "Радость"),
    ("excitement", "Возбуждение / азарт"),
    ("interest", "Интерес"),
    ("thinking", "Задумчивость"),
    ("sadness", "Грусть"),
    ("anger", "Злость"),
    ("fear", "Тревога / страх"),
    ("indifference", "Безразличие / усталость"),
    ("boredom", "Скука"),
]
