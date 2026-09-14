"""Простейшая шина событий: модули общаются через подписки, а не прямыми
импортами друг друга. Это позволяет редактировать любой модуль изолированно.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

log = logging.getLogger("events")


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Callable]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Callable) -> None:
        self._subs[topic].append(handler)

    def publish(self, topic: str, **payload) -> None:
        for handler in list(self._subs.get(topic, ())):
            try:
                handler(**payload)
            except Exception:  # noqa: BLE001 — ошибка одного слушателя не роняет шину
                log.exception("[ERROR] слушатель события %s упал", topic)
