"""Модуль LLM: локальная дообученная модель Нимфеи через Ollama.

Модель: models/nimfea-q4_k_m.gguf (gemma3:4b + LoRA, мультимодальная), в
Ollama по умолчанию `gemma3:4b` (см. core.config.LLM_MODEL и models/README.md
— Modelfile + `ollama create`; после переобучения курива → NIMA_LLM_MODEL=nimfea).
Модуль не знает ни про память, ни про промпты: он получает готовые messages
и возвращает текст. Стриминг — NDJSON /api/chat.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request

from core.config import (LLM_KEEP_ALIVE, LLM_MODEL, LLM_NUM_CTX, LLM_REPEAT_PENALTY,
                         LLM_TEMPERATURE, LLM_TIMEOUT, LLM_TOP_P, OLLAMA_URL)

log = logging.getLogger("llm")


def _sampling_options() -> dict:
    """Общие настройки сэмплинга (переопределяются env-переменными NIMA_LLM_*).

    Дефолты подобраны под связность 4B на русском (v14.8.51):
    - temperature 0.6 (было 0.7) — меньше «словесного салата»;
    - top_k 40 + min_p 0.05 — отсекают маловероятную ерунду, характер не трогают;
    - presence_penalty 0.0 (было 0.3) — не выталкивает модель с темы (причина
      ответов «невпопад» у маленькой модели).
    - num_predict 200 — потолок длины ответа (persona = 1-3 предложения):
      без него из Modelfile прилетает num_predict 32768 и ответ может
      «поехать» в простыню на десятки секунд (латентность стрима).
    LLM_TEMPERATURE из core.config остаётся совместимым: тот же NIMA_LLM_TEMPERATURE.
    """
    return {"num_ctx": LLM_NUM_CTX,
            "num_predict": int(os.environ.get("NIMA_LLM_NUM_PREDICT", "200")),
            "temperature": float(os.environ.get("NIMA_LLM_TEMPERATURE", "0.6")),
            "top_p": LLM_TOP_P,
            "top_k": int(os.environ.get("NIMA_LLM_TOP_K", "40")),
            "min_p": float(os.environ.get("NIMA_LLM_MIN_P", "0.05")),
            "repeat_penalty": LLM_REPEAT_PENALTY,
            "repeat_last_n": 128,
            "presence_penalty": float(os.environ.get("NIMA_LLM_PRESENCE_PENALTY", "0.0"))}


class LLMError(RuntimeError):
    pass


class LLMModule:
    def __init__(self, model: str = LLM_MODEL, base_url: str = OLLAMA_URL) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.last_error: str = ""

    # --- низкоуровневый запрос ---
    def _post(self, path: str, payload: dict, timeout: float = LLM_TIMEOUT) -> urllib.request.urlopen:  # type: ignore[name-defined]
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=timeout)

    # --- жизнь модели ---
    def is_alive(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=4) as resp:
                models = [m.get("name", "") for m in json.loads(resp.read()).get("models", [])]
                return any(m.split(":")[0] == self.model for m in models)
        except Exception:
            return False

    def warmup(self, retries: int = 3, pause: float = 5.0) -> bool:
        """Прогрев: грузим модель в VRAM и генерируем пустой ответ.

        На старте Ollama может отдать 500 (гонка за VRAM) — ретраим.
        """
        for attempt in range(1, retries + 1):
            try:
                t0 = time.time()
                with self._post("/api/generate", {
                    "model": self.model, "prompt": "", "keep_alive": LLM_KEEP_ALIVE,
                    "options": {"num_predict": 1, "num_ctx": LLM_NUM_CTX},
                }, timeout=180) as resp:
                    json.loads(resp.read())
                log.info("LLM прогрелась за %.1f с (модель %s)", time.time() - t0, self.model)
                return True
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                log.warning("[WARNING] прогрев LLM попытка %d/%d не удалась: %s",
                            attempt, retries, exc)
                if attempt < retries:
                    time.sleep(pause)
        log.error("LLM не прогрелась за %d попыток — ответы будут недоступны", retries)
        return False

    # --- генерация ---
    def generate(self, system: str, history: list[dict], user_text: str) -> str | None:
        """Обычная генерация (без стрима). Возвращает None при ошибке."""
        messages = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": user_text})
        try:
            with self._post("/api/chat", {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "keep_alive": LLM_KEEP_ALIVE,
                "options": _sampling_options(),
            }) as resp:
                data = json.loads(resp.read())
            text = (data.get("message") or {}).get("content", "").strip()
            return text or None
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.error("LLM generate: %s", exc)
            return None

    # --- микрогенерация (классификаторы, переспросы) ---
    def generate_micro(self, system: str, user_text: str, num_predict: int = 3,
                       timeout: float = 30.0) -> str | None:
        """Дешёвый ответ на 1-3 токена (ears-классификатор и т.п.)."""
        try:
            with self._post("/api/chat", {
                "model": self.model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user_text}],
                "stream": False,
                "keep_alive": LLM_KEEP_ALIVE,
                # num_ctx = LLM_NUM_CTX (а не 512), чтобы Ollama НЕ перезагружала
                # gemma под другой размер контекста. Разный num_ctx между
                # диалогом/vision/micro заставлял llama-server рестартовать даже
                # при свободной VRAM (см. logs/ollama_serve.log: -c 8192 ↔ -c 2048).
                "options": {"num_ctx": LLM_NUM_CTX, "num_predict": num_predict,
                            "temperature": 0.1},
            }, timeout=timeout) as resp:
                data = json.loads(resp.read())
            return ((data.get("message") or {}).get("content", "").strip() or None)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.warning("[WARNING] LLM micro: %s", exc)
            return None

    # --- зрение: gemma3:4b мультимодальная — картинка идёт в ТОТ же Ollama ---
    def generate_vision(self, system: str, user_text: str, image_jpeg: bytes,
                        num_predict: int = 110, timeout: float = 90.0) -> str | None:
        """Описание скриншота той же моделью (без второй VLM: на 6 ГБ своп
        моделей — десятки секунд тишины). num_predict маленький: ответ в 1-2
        предложения укладывается в бюджет ~2-4 с на GTX 1660 SUPER."""
        import base64
        b64 = base64.b64encode(image_jpeg).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user_text, "images": [b64]}],
            "stream": False,
            "keep_alive": LLM_KEEP_ALIVE,
            # num_ctx = LLM_NUM_CTX (а не 2048): единый контекст со всеми
            # остальными вызовами gemma — иначе Ollama пересобирает модель под
            # каждый новый размер и llama-server перезапускается (даже при
            # свободной VRAM). Единый ctx = модель грузится один раз и висит.
            "options": {"num_ctx": LLM_NUM_CTX, "num_predict": num_predict, "temperature": 0.2},
        }
        try:
            with self._post("/api/chat", payload, timeout=timeout) as resp:
                data = json.loads(resp.read())
            return ((data.get("message") or {}).get("content", "").strip() or None)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.warning("[WARNING] LLM vision: %s", exc)
            return None

    def generate_stream(self, system: str, history: list[dict], user_text: str):
        """Генерация чанками (генератор строк). При ошибке — yield всего None-нет,
        просто завершается; вызывающий сам решает, что делать с пустотой."""
        messages = [{"role": "system", "content": system}]
        messages += history
        messages.append({"role": "user", "content": user_text})
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": LLM_KEEP_ALIVE,
            "options": _sampling_options(),
        }
        try:
            with self._post("/api/chat", payload) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.error("LLM stream: %s", exc)
