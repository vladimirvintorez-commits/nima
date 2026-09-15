"""Построитель промпта: persona + память + история диалога + нити.

На вход берёт контекст из модуля памяти (memory/) и текст пользователя,
на выходе даёт готовый system-текст. Последний собранный промпт сохраняется
в cache/last_prompt.txt — его показывает debug menu (ПРОМПТ).
"""
from __future__ import annotations

import logging

from core import config
from core.config import LAST_PROMPT_PATH, MEMORY_HISTORY_LIMIT  # noqa: F401
from prompts.persona import (PERSONA, PERSONA_COMPACT, STREAM_MODE_OFF,
                             STREAM_MODE_ON, VISION_SENSE, time_sense)

log = logging.getLogger("prompts")

# Маппинг настроения (детектор пайплайна отдаёт англ.) → русское описание для промпта
MOOD_RU = {
    "anger": "злишься/раздражена",
    "sadness": "грустишь",
    "joy": "радуешься, весёлая",
    "excitement": "на подъёме, воодушевлена",
    "interest": "заинтересована, любопытно",
    "thinking": "задумчивая",
    "fear": "встревожена",
    "shyness": "смущена, стесняешься",
    "arousal": "возбуждена",
    "indifference": "всё безразлично",
    "boredom": "скучаешь",
    "sleeping": "сонная, устала",
    "normal": "обычное",
}


class PromptBuilder:
    def __init__(self, memory) -> None:  # memory.memory_module.MemoryModule
        self.memory = memory
        self.threads_summary = None   # callable() → str (хвосты от pipeline.threads)
        self.vision_summary = None    # callable() → str (наблюдения от vision)

    def build_system(self, user_text: str | None = None, mood: str | None = None,
                     speaker: str | None = None, *, extra: str = "",
                     present: list[str] | None = None) -> str:
        # режим стрима динамический: NIMA_STREAM_MODE или авто (есть Twitch = стрим)
        stream_on = config.STREAM_ACTIVE

        # === СТАБИЛЬНЫЙ ПРЕФИКС (кэшируется Ollama, не пересчитывается) ===
        # Порядок критичен: всё НЕИЗМЕННОЕ от запроса к запросу идёт СТРОГО в
        # начало. Ollama кэширует общий префикс токенов промпта (prompt cache) и
        # пропускает его prompt-eval — а это самая дорогая часть (см.
        # logs/ollama_serve.log: prompt eval ~16 c на ~3700 токенов). Как только
        # в префиксе что-то меняется, весь хвост считается заново. Поэтому
        # time_sense/память/vision-наблюдения/настроение — НИЖЕ, в динамике.
        # VISION_SENSE тут — только СТАТИЧНАЯ инструкция «ты видишь экран»;
        # сами наблюдения (меняются) добавляются в динамическом хвосте.
        # Персона: компактная (характер уже в весах дообученной модели) —
        # экономит ~1500 токенов prompt-eval на каждый ответ. NIMA_PERSONA_COMPACT=0
        # возвращает полную PERSONA (нужна для НЕдообученной базовой gemma3).
        persona = PERSONA_COMPACT if config.PERSONA_COMPACT else PERSONA
        blocks = [persona.strip(),
                  (STREAM_MODE_ON if stream_on else STREAM_MODE_OFF).strip()]
        if self.vision_summary and config.VISION_ENABLED:
            blocks.append(VISION_SENSE.strip())

        # === ДИНАМИЧЕСКИЙ ХВОСТ (меняется каждый запрос — пересчитывается) ===
        blocks.append(time_sense().strip())   # ритм дня: ночью сонная, вечером своя

        context = self.memory.get_prompt_context(query=user_text, speaker=speaker)
        if context:
            blocks.append("Контекст памяти (кто говорит, релевантные факты, что ты вспоминаешь):\n" + context)

        if present:
            from memory.memory_module import PLACEHOLDER_NAME_RE
            desc = ["(имя неизвестно — голос незнакомый, спроси, как его зовут)"
                    if PLACEHOLDER_NAME_RE.match(p.strip()) else p
                    for p in present]
            note = ("Сейчас с тобой говорят (голоса распознаны): "
                    + ", ".join(desc)
                    + ". Кого нет в списке — того нет в комнате, к нему не "
                      "обращайся. У того, чьё имя неизвестно, спроси имя.")
            # Автор последней реплики — явно: маленькой модели нужно знать, КОМУ
            # она отвечает, иначе имя собеседника в ответ почти не попадает
            # (баг «друг распознан по голосу, но обращения по имени нет»).
            if speaker and not PLACEHOLDER_NAME_RE.match(speaker.strip()):
                note += f" Прямо сейчас тебе говорит {speaker} — отвечаешь именно ему."
            note += (" Зови собеседника по имени, когда здороваешься, соглашаешься "
                     "или споришь, или задаёшь ему прямой вопрос — но не в каждой "
                     "реплике, только когда это живо звучит.")
            blocks.append(note)

        if self.threads_summary:
            tails = self.threads_summary()
            if tails:
                blocks.append(tails)

        # зрение: последние наблюдения (сама инструкция VISION_SENSE уже в
        # префиксе выше — здесь только меняющиеся данные наблюдений). Подаём
        # как ФОН: маленькая модель иначе перескакивает на экран вместо ответа.
        if self.vision_summary:
            seen = self.vision_summary()
            if seen:
                blocks.append("Фоном на экране (справочно, не главная тема — "
                              "заговаривай об этом, только если это к месту в "
                              "текущем разговоре или тебя спросили про экран; сам "
                              "на экран не перескакивай):\n" + seen)

        if mood and mood != "normal":
            blocks.append(
                f"Твоё текущее настроение: {MOOD_RU.get(mood, mood)} — оно осталось "
                "с прошлых реплик. Продолжай говорить и вести себя в нём (тег "
                "[ЭМОЦИЯ: …] ставь в тон настроению), пока что-то по-настоящему "
                "не сменит его. Но оставайся собой.")

        if extra:
            blocks.append(extra)

        system = "\n\n".join(blocks)
        try:
            LAST_PROMPT_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAST_PROMPT_PATH.write_text(system, encoding="utf-8")
        except OSError as exc:
            log.warning("[WARNING] не сохранить cache/last_prompt.txt: %s", exc)
        return system

    def get_history(self, speaker: str | None = None) -> list[dict]:
        """История текущего разговора; при известном голосе фильтруется по нему."""
        return self.memory.get_recent_dialog(limit=MEMORY_HISTORY_LIMIT, speaker=speaker)
