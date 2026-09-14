import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from memory.memory_module import MemoryModule
from pipeline.pipeline import DialoguePipeline
from prompts.prompt_builder import PromptBuilder


def make_memory(data):
    root = Path(tempfile.mkdtemp())
    path = root / "memory.json"
    path.write_text(json.dumps({"layers": data}, ensure_ascii=False), encoding="utf-8")
    with patch.object(MemoryModule, "start_embedding_index", lambda self: None):
        return MemoryModule(path)


class ResponseQualityTests(unittest.TestCase):
    def test_embedding_only_recall_rejects_weak_similarity(self):
        memory = make_memory({
            "dialog": [], "people": {}, "user_context": {},
            "facts": [
                {"id": "weak", "text": "рецепт яблочного пирога", "active": True},
                {"id": "strong", "text": "вечерняя игра", "active": True},
            ],
        })
        memory._query_vector = lambda _: np.asarray([1.0, 0.0], dtype=np.float32)
        memory._embed["weak"] = np.asarray([0.2, 0.98], dtype=np.float32).tobytes()
        memory._embed["strong"] = np.asarray([0.9, 0.1], dtype=np.float32).tobytes()
        self.assertEqual(["вечерняя игра"], memory.recall("квазары", limit=5))

    def test_history_excludes_service_and_other_speaker_turns(self):
        memory = make_memory({
            "facts": [], "people": {}, "user_context": {},
            "dialog": [
                {"role": "user", "text": "чужая тема", "speaker": "Вася", "user": "loop"},
                {"role": "bot", "text": "ответ Васе", "user": "loop"},
                {"role": "user", "text": "служебная тема", "user": "initiative"},
                {"role": "bot", "text": "служебный ответ", "user": "initiative"},
                {"role": "user", "text": "моя тема", "speaker": "Кизилл", "user": "stt"},
                {"role": "bot", "text": "ответ мне", "user": "stt"},
            ],
        })
        history = memory.get_recent_dialog(8, speaker="Кизилл")
        text = " ".join(item["content"] for item in history)
        self.assertIn("моя тема", text)
        self.assertIn("ответ мне", text)
        self.assertNotIn("чужая тема", text)
        self.assertNotIn("служеб", text)

    def test_prompt_context_uses_current_person_not_global_people_dump(self):
        memory = make_memory({
            "dialog": [], "facts": [], "user_context": {},
            "people": {
                "vasya": {"name": "Вася", "traits": ["любит футбол"]},
                "kizill": {"name": "Кизилл", "traits": ["стримит доту"]},
            },
        })
        memory._query_vector = lambda _: None
        context = memory.get_prompt_context("дота", speaker="Кизилл")
        self.assertIn("Кизилл", context)
        self.assertNotIn("Вася", context)
        system = PromptBuilder(memory).build_system("дота", speaker="Кизилл")
        self.assertIn("Кизилл", system)

    def test_stream_cleanup_deduplicates_and_caps_sentences(self):
        source = iter([
            "Первое предложение.",
            "Первое предложение.",
            "Второе предложение.",
            "Третье предложение.",
            "Четвёртое предложение.",
        ])
        self.assertEqual([
            "Первое предложение.", "Второе предложение.", "Третье предложение."
        ], list(DialoguePipeline._clean_sentences(source)))


if __name__ == "__main__":
    unittest.main()
