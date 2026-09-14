"""Одежда Нимфеи: просьбы переодеться + защита от чужих просьб.

ЗАЩИТА (v14.2) — в коде, а не на совести модели: сменить образ (включая
раздеться) может ТОЛЬКО стример — его микрофон (после гейта адресации) или
ручная консоль debug menu. Чат, донаты и loopback до детектора не доходят:
пайплайн вызывает детект только для TRUSTED_SOURCES.

Детект эвристический (быстрый, без LLM): намерение смены одежды + цель.
Голые команды («разденься») сами являются намерением. Без цели — «переоденься» —
образ выбирается случайно из повседневного пула (голое/сексуальное — только
по явной просьбе, в случайный выбор не попадает).
"""
from __future__ import annotations

import random
import re

# Кому разрешено менять Нимфею одежду: микрофон стримера и ручная консоль.
# НЕ входят: loopback (звук системы), twitch (чат), donation (зрители).
TRUSTED_SOURCES = ("stt", "manual")

# «переоденься» без конкретной цели
OUTFIT_RANDOM = "@random"

# Явные команды раздеться — сами по себе намерение, цель не нужна
_NAKED_RE = re.compile(
    r"(?<!\w)(раздень(?:ся|те)?|раздева\w*|раздеться|раздену|скинь одежд\w*|"
    r"сбрось одежд\w*|стань голой|хочу тебя голой|голая|голую|naked)(?!\w)",
    re.IGNORECASE)

# Намерение смены одежды (нужна ПЛЮС цель из _OUTFIT_TARGETS)
_CHANGE_RE = re.compile(
    r"(?<!\w)(переодень\w*|переодеться|смени одежд\w*|надень(?:те|ка)?\b|"
    r"надевай\b|оденься|примерь\b|померь\b|друг\w+ (?:одежд\w*|образ\w*|наряд\w*))",
    re.IGNORECASE)

# Цели: более частные паттерны раньше. Проверяются только при намерении.
_OUTFIT_TARGETS: list[tuple[str, str]] = [
    (r"(?<!\w)кимоно|kimono", "Nima_kimono.vrm"),
    (r"(?<!\w)горничн\w*|мейд|maid", "Nima_maid.vrm"),
    (r"(?<!\w)джинс\w*|jeans|jeens", "Nima_jeens.vrm"),
    (r"(?<!\w)плать\w*|drees|dress", "Nima_drees.vrm"),
    (r"(?<!\w)сексуальн\w*|эротичн\w*|sexual", "Nima_Sexual.vrm"),
    (r"(?<!\w)стандартн\w*|обычн\w*(?:ую|ые|ое)|standart|standard", "Nima_standart.vrm"),
]

# Человеческие имена образов для реплик
OUTFIT_LABELS = {
    "Nima_standart.vrm": "привычный образ",
    "Nima_drees.vrm": "платье",
    "Nima_jeens.vrm": "джинсы",
    "Nima_kimono.vrm": "кимоно",
    "Nima_maid.vrm": "костюм горничной",
    "Nima_naked.vrm": "голое тело",
    "Nima_Sexual.vrm": "сексуальный наряд",
}

# Пул для «переоденься» без цели и собственной инициативы:
# повседневные образы. Nima_naked / Nima_Sexual — только по явной просьбе.
OUTFIT_SELF_POOL = [
    "Nima_standart.vrm", "Nima_drees.vrm", "Nima_jeens.vrm",
    "Nima_kimono.vrm", "Nima_maid.vrm",
]

_FALLBACK_REMARKS = [
    "Та-дам! Ну как, зашло?",
    "Секунду, переоденусь… Ну что, теперь-то хорошо?",
    "Смотри и не отводи глаза.",
    "М-м-м, вроде моё. Как считаешь?",
]


def detect_outfit_request(text: str) -> str | None:
    """Просьба сменить одежду → имя VRM или OUTFIT_RANDOM; иначе None."""
    if not text:
        return None
    if _NAKED_RE.search(text):
        return "Nima_naked.vrm"
    if not _CHANGE_RE.search(text):
        return None
    for pattern, outfit in _OUTFIT_TARGETS:
        if re.search(pattern, text, re.IGNORECASE):
            return outfit
    return OUTFIT_RANDOM


def pick_random_outfit(current: str | None) -> str | None:
    """Другой повседневный образ, не совпадающий с текущим."""
    pool = [o for o in OUTFIT_SELF_POOL if o != current]
    return random.choice(pool) if pool else None


def fallback_remark(index: int) -> str:
    return _FALLBACK_REMARKS[index % len(_FALLBACK_REMARKS)]
