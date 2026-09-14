"""voice_id — кто говорит: идентификация по голосу (требование 9).

Эмбеддинг каждой фразы сравнивается с профилями известных людей
(memory.people[].voice_embedding): похож (косинус ≥ VOICE_ID_THRESHOLD) —
говорящий опознан, иначе заводится новый профиль «Друг N».

Движок: speechbrain ECAPA-TDNN (CPU, ~0.2 с на фразу, без VRAM). Если пакет
не установлен — деградация: канал mic = владелец (NIMA_VOICE_ID_USER),
канал loop = «Собеседник» (имена обновляются, как только пользователь
представит друга — memory.people).

Профили живут в памяти (memory.set_person_embedding) и уточняются со временем:
каждый опознанный голос слегка подтягивает профиль (скользящее среднее).
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
from pathlib import Path

import numpy as np

from core.config import (CACHE_DIR, VOICE_ID_ENABLED, VOICE_ID_MIN_NEW_SEC,
                         VOICE_ID_SOFT_THRESHOLD, VOICE_ID_THRESHOLD, VOICE_ID_USER)

# HF-кэш без symlinks: без админ-привилегий их создание падает (WinError 1314)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

log = logging.getLogger("voice_id")

_SAMPLE_RATE = 16000

# Файлы модели ECAPA (репо speechbrain/spkrec-ecapa-voxceleb; custom.py в нём
# нет). label_encoder.ckpt в репо отсутствует, но speechbrain запрашивает
# именно его — кладём рядом копию label_encoder.txt.
_ECAPA_FILES = ("hyperparams.yaml", "embedding_model.ckpt", "mean_var_norm_emb.ckpt",
                "classifier.ckpt", "label_encoder.txt")


def _prepare_savedir() -> str:
    """Абсолютный cache/ecapa с файлами модели БЕЗ symlinks.

    speechbrain тянет модель через huggingface_hub, который на Windows без прав
    администратора/Developer Mode падает на создании symlink в savedir
    (WinError 1314) — и ECAPA никогда не грузилась. Лечим: если файла нет —
    достаём его из HF-кэша через hf_hub_download (кэш работает и без symlinks,
    файл кладётся обычной копией) и копируем в savedir обычным shutil.copy.
    """
    savedir = CACHE_DIR / "ecapa"
    savedir.mkdir(parents=True, exist_ok=True)
    if not (savedir / "label_encoder.ckpt").exists() \
            and (savedir / "label_encoder.txt").exists():
        shutil.copy(savedir / "label_encoder.txt", savedir / "label_encoder.ckpt")
    missing = [name for name in _ECAPA_FILES if not (savedir / name).exists()]
    if not missing:
        return str(savedir)
    try:
        from huggingface_hub import hf_hub_download
        for name in missing:
            src = hf_hub_download("speechbrain/spkrec-ecapa-voxceleb", name)
            shutil.copy(src, savedir / name)
        if (savedir / "label_encoder.txt").exists() \
                and not (savedir / "label_encoder.ckpt").exists():
            shutil.copy(savedir / "label_encoder.txt", savedir / "label_encoder.ckpt")
        log.info("voice_id: ECAPA (%d файлов) скопирована в %s без symlinks",
                 len(missing), savedir)
    except Exception as exc:  # noqa: BLE001 — нет сети/пакета: ниже деградация
        log.warning("[WARNING] voice_id: не заполнить cache/ecapa (%s)", exc)
    return str(savedir)


class VoiceID:
    def __init__(self, memory) -> None:
        self.memory = memory
        self.enabled = VOICE_ID_ENABLED
        self._encoder = None            # speechbrain модель (лениво)
        self._encoder_failed = False
        self._lock = threading.Lock()

    # --- движок ---
    def _get_encoder(self):
        with self._lock:
            if self._encoder is not None or self._encoder_failed:
                return self._encoder
            try:
                from speechbrain.inference.speaker import EncoderClassifier
                savedir = _prepare_savedir()
                if not (Path(savedir) / "hyperparams.yaml").exists():
                    raise RuntimeError(f"cache/ecapa не заполнен: {savedir}")
                self._encoder = EncoderClassifier.from_hparams(
                    source="speechbrain/spkrec-ecapa-voxceleb",
                    savedir=savedir,
                    run_opts={"device": "cpu"})
                log.info("voice_id: ECAPA загружена (CPU)")
            except Exception as exc:  # noqa: BLE001 — пакета нет/нет сети
                self._encoder_failed = True
                log.info("voice_id: работаю по каналам без ECAPA (%s)", exc)
            return self._encoder

    def embedding(self, audio: np.ndarray) -> np.ndarray | None:
        """Эмбеддинг фразы (float32, mono 16 кГц) или None."""
        if not self.enabled:
            return None
        encoder = self._get_encoder()
        if encoder is None:
            return None
        try:
            import torch
            waveform = torch.tensor(audio, dtype=torch.float32).unsqueeze(0)
            if waveform.shape[-1] < _SAMPLE_RATE // 2:  # <0.5 с — ненадёжно
                return None
            with torch.no_grad():
                emb = encoder.encode_batch(waveform)
            vec = emb.squeeze().cpu().numpy().astype(np.float32)
            norm = float(np.linalg.norm(vec))
            return vec / norm if norm > 0 else None
        except Exception as exc:  # noqa: BLE001
            log.warning("[WARNING] voice_id embedding: %s", exc)
            return None

    # --- опознание ---
    def identify(self, audio: np.ndarray, channel: str) -> str:
        """Имя говорящего: по голосу → профиль в памяти, иначе по каналу.

        Канал mic — это микрофон ВЛАДЕЛЬЦА (конфиг VOICE_ID_USER): чужие
        профили с него не присваиваем и новые «Друга N» не заводим — иначе
        голос владельца уходит в чужие профили и засоряет память (живой тест
        13.09: 47 «Другов» из одного голоса владельца). Микрофонные фразы
        только уточняют профиль владельца; все прочие люди — через канал
        loop (созвон) или представляются словами.

        Гистерезис (урок 11.09: каждая фраза плодила «Друга N»):
          ≥ VOICE_ID_THRESHOLD       — точно опознан, эталон добавляется;
          ≥ VOICE_ID_SOFT_THRESHOLD  — вероятно тот же человек (фраза короче/
                                       шумнее): прикрепляем к лучшему профилю,
                                       нового не заводим;
          ниже                       — новый профиль ТОЛЬКО если фраза длинная
                                       (короткие обрывки/шум профили не создают).
        """
        owner = VOICE_ID_USER.strip()
        default = owner if channel == "mic" else "Собеседник"
        emb = self.embedding(audio)
        if emb is None:
            return default

        best_name, best_score = None, -1.0
        for name, embeddings in self.memory.people_with_embeddings():
            for profile in embeddings:
                score = float(np.dot(emb, np.asarray(profile, dtype=np.float32)))
                if score > best_score:
                    best_name, best_score = name, score

        if channel == "mic":
            # микрофон = владелец: уточняем ТОЛЬКО его профиль
            if best_name and best_name.strip().lower() == owner.lower() \
                    and best_score >= VOICE_ID_SOFT_THRESHOLD:
                self.memory.update_person_embedding(best_name, emb.tolist())
                return best_name
            seconds = len(audio) / _SAMPLE_RATE if audio is not None else 0.0
            if seconds >= VOICE_ID_MIN_NEW_SEC:
                # короткие обрывки профиль не двигают; длинная фраза — эталон
                self.memory.set_person_embedding(owner, emb.tolist())
                if best_name and best_name.strip().lower() != owner.lower() \
                        and best_score >= VOICE_ID_THRESHOLD:
                    log.info("voice_id: микрофон похож на «%s» (%.2f), но это "
                             "канал владельца — записываю как %s",
                             best_name, best_score, owner)
                return owner
            return default

        if best_name and best_score >= VOICE_ID_THRESHOLD:
            return best_name
        if best_name and best_score >= VOICE_ID_SOFT_THRESHOLD:
            log.info("voice_id: %s (мягкая схожесть %.2f < %.2f) — без нового профиля",
                     best_name, best_score, VOICE_ID_THRESHOLD)
            return best_name

        # loop — микс созвона и фонового видео (урок 13.09: схожесть скачет
        # 0.27–0.68, мусорные «Друга N» и расползание эталонов). Новые профили
        # и дозревание здесь запрещены: профиль собеседника строит ТОЛЬКО
        # ручной энроллмент (debug menu).
        log.info("voice_id: loop, голос незнаком (схожесть %.2f) — «Собеседник» "
                 "(профили на loop не заводим)", best_score)
        return default

    # --- энроллмент ---
    def enroll(self, name: str, audio: np.ndarray) -> bool:
        """Записать/перезаписать профиль голоса вручную (debug menu)."""
        emb = self.embedding(audio)
        if emb is None:
            return False
        self.memory.set_person_embedding(name, emb.tolist())
        return True
