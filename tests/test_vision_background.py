import time
import unittest
from unittest.mock import patch

import numpy as np

from vision.vision_module import VisionModule, _motion_value, _overlap


class Memory:
    def __init__(self):
        self.facts = []

    def append_fact(self, text, **kwargs):
        self.facts.append(text)


class LLM:
    def __init__(self, text="На экране открыта игра."):
        self.text = text

    def generate_vision(self, *args):
        return self.text


class VisionBackgroundTests(unittest.TestCase):
    def test_scene_change_and_text_overlap_are_deterministic(self):
        a = np.zeros((4, 4), dtype=np.uint8)
        b = np.full((4, 4), 20, dtype=np.uint8)
        self.assertEqual(20.0, _motion_value(a, b))
        self.assertGreater(_overlap("Открыта игра Dota на экране", "На экране открыта игра Dota"), .7)
        self.assertLess(_overlap("Открыта игра Dota", "Редактор кода с тестами"), .3)

    def test_describe_now_preserves_recent_and_memory(self):
        memory = Memory()
        shot = (b"jpeg", np.zeros((4, 4), dtype=np.uint8))
        vision = VisionModule(memory, LLM(), capture_fn=lambda: shot)
        with patch("vision.vision_module.VISION_FACT_MIN_SEC", 0):
            text = vision.describe_now()
        self.assertEqual("На экране открыта игра.", text)
        self.assertEqual(text, vision.recent[-1]["text"])
        self.assertTrue(memory.facts)

    def test_comment_schedule_dedup_cooldown_busy_and_activity_guard(self):
        vision = VisionModule(Memory(), LLM(), capture_fn=lambda: None)
        sent = []
        vision.on_observation = lambda text, kind: sent.append((text, kind))
        vision.busy_check = lambda: False
        now = time.time()
        vision._last_user_ts = now - 100
        with patch("vision.vision_module.VISION_COMMENT_INTERRUPT_GUARD_SEC", 0), \
             patch("vision.vision_module.VISION_COMMENT_COOLDOWN_SEC", 60), \
             patch("vision.vision_module.VISION_OBSERVATION_TTL_SEC", 120):
            vision._pending_comment = {"ts": now - 10, "due": now - 1,
                                       "text": "Открылась новая игра", "seq": 0}
            vision._dispatch_comment(now)
            self.assertEqual([("Открылась новая игра", "comment")], sent)
            self.assertFalse(vision._is_novel_comment("Открылась новая игра", 20))
            vision._pending_comment = {"ts": now, "due": now,
                                       "text": "Открылся редактор", "seq": 0}
            vision._dispatch_comment(now + 10)
            self.assertEqual(1, len(sent))
            # реплика человека откладывает комментарий, но НЕ убивает его
            vision.note_user()
            self.assertIsNotNone(vision._pending_comment)
            self.assertGreaterEqual(vision._pending_comment["due"], time.time())

    def test_busy_defers_without_consuming_comment(self):
        vision = VisionModule(Memory(), LLM(), capture_fn=lambda: None)
        vision._last_user_ts = time.time() - 100
        vision.busy_check = lambda: True
        vision._pending_comment = {"ts": time.time(), "due": 0,
                                   "text": "Сменилась сцена", "seq": 0}
        vision._dispatch_comment(time.time())
        self.assertIsNotNone(vision._pending_comment)


if __name__ == "__main__":
    unittest.main()
