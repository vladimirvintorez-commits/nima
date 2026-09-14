"""Устойчивое настроение Нимфы между репликами.

Одноразовый детект слов (_detect_mood) задавал настроение только на одну
фразу, а её собственные теги [ЭМОЦИЯ: …] жили лишь внутри текущего ответа —
на следующем ходе LLM генерил «с нуля». Этот модуль держит настроение ЖИВЫМ:

  • тег [ЭМОЦИЯ: …] от самой Нимфы — сильный источник: её настоящее
    состояние, тянется несколько ходов;
  • слова пользователя — слабый: она «заражается» настроением собеседника,
    только пока сама в норме (своё состояние слова не перебивают);
  • каждый ход настроение затухает и в покое возвращается к normal.

Текущее настроение забирают двое: prompt_builder (в system — чтобы LLM
ПИСАЛА в этом настроении) и tts/avatar (голос и лицо, как раньше).
"""
from __future__ import annotations

import logging

log = logging.getLogger("pipeline")

# сколько ходов держится настроение (сила источника)
TURNS_FROM_TAG = 3    # своё состояние по тегу [ЭМОЦИЯ: …]
TURNS_FROM_USER = 2   # заражение от слов пользователя

MOODS = frozenset({
    "normal", "joy", "excitement", "interest", "thinking", "sadness",
    "anger", "fear", "shyness", "arousal", "indifference", "boredom",
    "sleeping",
})


class MoodState:
    """Текущее настроение + счётчик ходов до возврата в normal."""

    def __init__(self) -> None:
        self._mood = "normal"
        self._turns_left = 0

    @property
    def mood(self) -> str:
        return self._mood

    @property
    def turns_left(self) -> int:
        return self._turns_left

    def observe_tag(self, mood: str) -> None:
        """[ЭМОЦИЯ: …] из ответа модели — её собственное состояние, сильный источник."""
        if mood and mood in MOODS and mood != "normal":
            self._mood = mood
            self._turns_left = TURNS_FROM_TAG
            log.info("mood: своё настроение по тегу: %s (держится %d ходов)",
                     mood, TURNS_FROM_TAG)

    def observe_user(self, mood: str) -> None:
        """Настроение, пойманное по словам пользователя: заражает, только
        пока Нимфа сама в норме — живое своё состояние не перебивается."""
        if mood and mood in MOODS and mood != "normal" \
                and (self._mood == "normal" or self._turns_left <= 0):
            self._mood = mood
            self._turns_left = TURNS_FROM_USER
            log.info("mood: заразилась настроением пользователя: %s", mood)

    def tick(self) -> None:
        """Ход прошёл: хвост настроения затухает, в покое — снова normal."""
        if self._turns_left > 0:
            self._turns_left -= 1
            if self._turns_left == 0:
                if self._mood != "normal":
                    log.info("mood: настроения выдохлось (%s) — снова normal", self._mood)
                self._mood = "normal"
