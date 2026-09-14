"""Проверка новой логики voice_id: многоэталонные профили, мягкое прикрепление,
запрет создавать профиль из коротких фраз. Без ECAPA — embedding подменяется."""
import sys, types
import numpy as np

sys.path.insert(0, r"B:\Neyronya")

from voice_id.voice_id_module import VoiceID


class FakeMemory:
    """Минимальная заглушка memory: люди + эмбеддинги в том же формате."""
    def __init__(self):
        self.people = {}

    def people_with_embeddings(self):
        return [(p["name"], p["embs"]) for p in self.people.values() if p["embs"]]

    def next_free_friend_name(self):
        n = 1
        while f"Друг {n}" in {p["name"] for p in self.people.values()}:
            n += 1
        return f"Друг {n}"

    def set_person_embedding(self, name, emb):
        self.people.setdefault(name, {"name": name, "embs": []})["embs"].append(emb)

    def update_person_embedding(self, name, emb):
        self.people[name]["embs"].append(emb)

    def rename_person(self, old, new):
        if old not in self.people:
            return False
        self.people[new] = self.people.pop(old)
        self.people[new]["name"] = new
        return True


def make_vec(rng, base=None, noise=0.2):
    v = base.copy() if base is not None else rng.normal(size=192)
    if base is not None:
        v = v + rng.normal(size=192) * noise
    v = v / np.linalg.norm(v)
    return v.astype(np.float32).tolist()


def run():
    rng = np.random.default_rng(7)
    mem = FakeMemory()
    vid = VoiceID(mem)
    vid.enabled = True
    vid._encoder_failed = True          # ECAPA не грузим
    voice_a = rng.normal(size=192); voice_a /= np.linalg.norm(voice_a)

    long_audio = np.zeros(32000, dtype=np.float32)   # 2 c
    short_audio = np.zeros(8000, dtype=np.float32)   # 0.5 c

    # 1. первая длинная фраза незнакомого голоса → Друг 1
    vid.embedding = lambda audio: np.asarray(make_vec(rng, voice_a, 0.02), dtype=np.float32)
    name1 = vid.identify(long_audio, "loop")
    assert name1 == "Друг 1", name1

    # 2. та же «сущность», схожесть ~0.6 (ниже точного порога 0.72, выше мягкого
    # 0.50) → НЕ новый друг, прикрепление к лучшему профилю
    vid.embedding = lambda audio: np.asarray(make_vec(rng, voice_a, 0.09), dtype=np.float32)
    mid = np.asarray(make_vec(rng, voice_a, 0.09), dtype=np.float32)
    ref = np.asarray(make_vec(rng, voice_a, 0.02), dtype=np.float32)
    print("sim(σ0.02 vs σ0.09) =", round(float(np.dot(mid, ref)), 3))
    name2 = vid.identify(long_audio, "loop")
    assert name2 == "Друг 1", f"ожидали прикрепление к Друг 1, получили {name2}"

    # 3. короткая фраза совсем незнакомого голоса → профиль НЕ создаётся
    vid.embedding = lambda audio: np.asarray(make_vec(rng), dtype=np.float32)
    name3 = vid.identify(short_audio, "loop")
    assert name3 == "Собеседник", name3
    assert len(mem.people) == 1, list(mem.people)

    # 4. длинная незнакомая фраза → Друг 2
    name4 = vid.identify(long_audio, "loop")
    assert name4 == "Друг 2", name4

    # 5. у Друг 1 накопилось несколько эталонов; знакомый вектор по-прежнему бьётся
    n_embs = len(mem.people["Друг 1"]["embs"])
    assert n_embs >= 2, n_embs
    vid.embedding = lambda audio: np.asarray(make_vec(rng, voice_a, 0.02), dtype=np.float32)
    name5 = vid.identify(long_audio, "loop")
    assert name5 == "Друг 1", name5

    # 6. переименование переносит эталоны
    assert mem.rename_person("Друг 1", "Вася")
    vid.embedding = lambda audio: np.asarray(make_vec(rng, voice_a, 0.05), dtype=np.float32)
    name6 = vid.identify(long_audio, "loop")
    assert name6 == "Вася", name6

    print("voice_id checks OK:",
          {"people": list(mem.people), "vasya_embs": len(mem.people["Вася"]["embs"])})


if __name__ == "__main__":
    run()
