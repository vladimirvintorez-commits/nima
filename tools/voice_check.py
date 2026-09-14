"""Диагностика voice_id: схожесть wav-файла со всеми голосовыми профилями.

Запуск (myenv):  python tools/voice_check.py <файл.wav> [ещё.wav ...]
Показывает косинусную схожесть эмбеддинга каждого файла со всеми профилями
из memory.json — видно сразу, кого ECAPA «узнаёт», а кого нет, и не слиплись
ли автопрофили «Друг N» с именными.
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MEMORY_PATH = PROJECT_ROOT / "memory.json"


def load_wav(path: Path) -> np.ndarray | None:
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            audio = np.frombuffer(wf.readframes(wf.getnframes()),
                                  dtype=np.int16).astype(np.float32) / 32768.0
    except Exception as exc:  # noqa: BLE001
        print(f"{path.name}: не прочитать ({exc})")
        return None
    if rate != 16000:  # ресемпл в 16 кГц
        audio = np.interp(np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / rate)),
                          np.arange(len(audio)), audio).astype(np.float32)
    return audio


def main() -> None:
    paths = [Path(a) for a in sys.argv[1:]]
    if not paths:
        print(__doc__)
        return
    from voice_id.voice_id_module import VoiceID

    vid = VoiceID(memory=None)
    profiles: dict[str, list[np.ndarray]] = {}
    try:
        data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        for key, person in (data.get("layers", {}).get("people", {}) or {}).items():
            embs = person.get("voice_embeddings") if isinstance(person, dict) else None
            if isinstance(embs, list) and embs:
                profiles[person.get("name") or key] = [np.asarray(e, dtype=np.float32)
                                                       for e in embs]
    except Exception as exc:  # noqa: BLE001
        print(f"не прочитать memory.json: {exc}")
        return

    for path in paths:
        audio = load_wav(path)
        if audio is None:
            continue
        emb = vid.embedding(audio)
        if emb is None:
            print(f"{path.name}: эмбеддинг не вычислен (тишина?)")
            continue
        print(f"\n{path.name} ({len(audio)/16000:.1f} с):")
        for name, embs in sorted(profiles.items()):
            best = max(float(np.dot(emb, p)) for p in embs)
            mark = "  ← совпал" if best >= 0.72 else ("  ~ близко" if best >= 0.5 else "")
            print(f"  {name}: {best:.3f} (эталонов: {len(embs)}){mark}")


if __name__ == "__main__":
    main()
