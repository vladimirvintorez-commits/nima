"""watchdog — сторожевой пёс (v14.7): стрим не должен умирать молча.

Уроки живых тестов v14.6: WASAPI-loopback умирал ДВАЖДЫ по разным причинам
(C-колбэки PyAudioWPatch не доставляли аудио; стрим уходил в stopped при
тишине рендер-устройства) — оба раза это ловилось только человеком за
клавиатурой. Watchdog проверяет живость сам и реанимирует:

  • STT-потоки (микрофон/loopback): поток захвата и поток распознавания
    должны быть живы; мёртвые → STTModule.restart() (модель уже в памяти);
  • Ollama: раз в WATCHDOG_OLLAMA_INTERVAL_SEC спрашивает /api/tags;
    недоступна → лог + флаг (пайплайн и так переживёт), вернулась → прогрев.

Проверки делаются колбэками (check → None | строка-проблема | (проблема, fix)),
так что модуль ничего не знает про устройство STT/LLM — только про контракт.
"""
from __future__ import annotations

import logging
import threading
import time

from core.config import (WATCHDOG_ENABLED, WATCHDOG_INTERVAL_SEC,
                         WATCHDOG_OLLAMA_INTERVAL_SEC)

log = logging.getLogger("watchdog")


class WatchdogModule:
    def __init__(self, enabled: bool = WATCHDOG_ENABLED) -> None:
        self.enabled = enabled
        self.issues: list[str] = []      # последние проблемы (для debug menu)
        self._checks: list = []          # callable() -> str | None | tuple(str, callable)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def register(self, name: str, check) -> None:
        """check() → None (ок) | str (проблема) | (str, fix_callable) (починить)."""
        self._checks.append((name, check))

    def start(self) -> None:
        if not self.enabled:
            log.info("watchdog выключен (NIMA_WATCHDOG=0)")
            return
        self._thread = threading.Thread(target=self._loop, name="Watchdog", daemon=True)
        self._thread.start()
        log.info("watchdog запущен (проверка раз в %.0f с, Ollama раз в %.0f с)",
                 WATCHDOG_INTERVAL_SEC, WATCHDOG_OLLAMA_INTERVAL_SEC)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        last_ollama = 0.0
        ollama_ok = True
        while not self._stop.is_set():
            self._stop.wait(WATCHDOG_INTERVAL_SEC)
            if self._stop.is_set():
                return
            self._run_checks()
            # Ollama опрашивается реже: это HTTP-запрос, а не локальный флаг
            now = time.time()
            if now - last_ollama >= WATCHDOG_OLLAMA_INTERVAL_SEC:
                last_ollama = now
                ok = self._ollama_check()
                if not ok and ollama_ok:
                    self._note("Ollama не отвечает — ответы Нимфеи недоступны")
                    log.warning("[WARNING] watchdog: Ollama не отвечает")
                elif ok and not ollama_ok:
                    log.info("watchdog: Ollama вернулась")
                ollama_ok = ok

    def _run_checks(self) -> None:
        """Один шаг проверок зарегистрированных колбэков (отделено для тестов)."""
        for name, check in self._checks:
            try:
                result = check()
            except Exception as exc:  # noqa: BLE001 — проверка не должна ронять пса
                log.warning("[WARNING] watchdog: проверка %s упала: %s", name, exc)
                continue
            if result is None:
                continue
            if isinstance(result, tuple):
                problem, fix = result
            else:
                problem, fix = result, None
            self._note(problem)
            log.warning("[WARNING] watchdog: %s", problem)
            if fix:
                try:
                    fix()
                    self.issues.append(f"{problem} → перезапущено")
                    log.info("watchdog: перезапустил после проблемы: %s", problem)
                except Exception as exc:  # noqa: BLE001
                    log.error("[ERROR] watchdog: починка не удалась: %s", exc)

    def _ollama_check(self) -> bool:
        try:
            import urllib.request
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5):
                return True
        except Exception:  # noqa: BLE001
            return False

    def _note(self, problem: str) -> None:
        self.issues.append(f"{time.strftime('%H:%M:%S')} {problem}")
        del self.issues[:-20]
