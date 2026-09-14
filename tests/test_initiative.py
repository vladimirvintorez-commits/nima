import unittest
from unittest.mock import patch

from initiative.initiative_module import InitiativeModule


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class Memory:
    def query(self, limit=6):
        return []

    def recall_dialog(self, text, limit=3):
        return []


class InitiativeTimerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.module = InitiativeModule(Memory(), clock=self.clock)

    def test_user_activity_controls_main_silence(self):
        with patch("initiative.initiative_module.INITIATIVE_SILENCE_SEC", 60):
            self.assertEqual("user_quiet", self.module.eligibility().reason)
            self.clock.advance(60)
            self.assertTrue(self.module.eligibility().allowed)
            self.module.note_activity("user")
            self.assertEqual("user_quiet", self.module.eligibility().reason)

    def test_chat_uses_short_gate_without_resetting_user_silence(self):
        with patch("initiative.initiative_module.INITIATIVE_SILENCE_SEC", 60), \
             patch("initiative.initiative_module.INITIATIVE_CHAT_QUIET_SEC", 18):
            self.clock.advance(60)
            self.module.note_activity("chat")
            state = self.module.eligibility()
            self.assertEqual("chat_active", state.reason)
            self.assertGreaterEqual(state.user_quiet, 60)
            self.clock.advance(18)
            self.assertTrue(self.module.eligibility().allowed)

    def test_normal_nima_reply_does_not_start_proactive_gap(self):
        with patch("initiative.initiative_module.INITIATIVE_SILENCE_SEC", 60), \
             patch("initiative.initiative_module.INITIATIVE_REPLY_QUIET_SEC", 12), \
             patch("initiative.initiative_module.INITIATIVE_MIN_GAP_SEC", 120):
            self.clock.advance(60)
            self.module.note_spoke()
            self.assertEqual("nima_reply", self.module.eligibility().reason)
            self.clock.advance(12)
            self.assertTrue(self.module.eligibility().allowed)

    def test_proactive_has_its_own_cooldown(self):
        with patch("initiative.initiative_module.INITIATIVE_SILENCE_SEC", 60), \
             patch("initiative.initiative_module.INITIATIVE_REPLY_QUIET_SEC", 12), \
             patch("initiative.initiative_module.INITIATIVE_MIN_GAP_SEC", 120):
            self.clock.advance(60)
            self.module.note_spoke(proactive=True)
            self.clock.advance(12)
            self.assertEqual("proactive_gap", self.module.eligibility().reason)
            self.clock.advance(108)
            self.assertTrue(self.module.eligibility().allowed)

    def test_live_web_is_disabled_by_default(self):
        class Web:
            def search(self, *args, **kwargs):
                raise AssertionError("live web must not run in critical path")

        module = InitiativeModule(Memory(), Web(), clock=self.clock)
        with patch("initiative.initiative_module.INITIATIVE_LIVE_WEB", False):
            source, _ = module._pick_topic()
        self.assertEqual("question", source)


if __name__ == "__main__":
    unittest.main()
