import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from memory.memory_module import MemoryModule
from stt.stt_module import STTModule
from tts.tts_module import TTSModule


class TraceRecorder:
    def __init__(self):
        self.events = []

    def mark(self, stage, request_id=None, **kwargs):
        self.events.append((stage, request_id, kwargs))


class LatencyPipelineTests(unittest.TestCase):
    def test_query_embedding_is_reused_for_fact_and_dialog_recall(self):
        root = Path(tempfile.mkdtemp())
        memory_path = root / "memory.json"
        memory_path.write_text(json.dumps({
            "layers": {
                "dialog": [{"id": f"d{i}", "role": "user", "text": "дота стрим",
                            "ts": f"2025-01-{i + 1:02d}"} for i in range(20)],
                "facts": [{"id": "f1", "text": "Кизил стримит доту", "active": True}],
                "people": {}, "user_context": {}
            }
        }, ensure_ascii=False), encoding="utf-8")
        with patch.object(MemoryModule, "start_embedding_index", lambda self: None):
            memory = MemoryModule(memory_path)
        calls = []
        memory._embed_texts = lambda texts: calls.append(texts) or [[1.0, 0.0]]
        memory.get_prompt_context("дота стрим")
        self.assertEqual(1, len(calls))

    def test_stt_callback_accepts_request_id_and_keeps_legacy_compatibility(self):
        received = []
        module = STTModule(lambda text, channel, audio, rid: received.append(rid))
        module._emit_utterance("тест", np.zeros(10), "rid-1")
        self.assertEqual(["rid-1"], received)

        legacy = []
        module = STTModule(lambda text, channel, audio: legacy.append(text))
        module._emit_utterance("тест", np.zeros(10), "rid-2")
        self.assertEqual(["тест"], legacy)

    def test_stt_channel_text_filter_keeps_mic_commands_and_rejects_loop_noise(self):
        mic = STTModule(lambda *_: None, channel="mic")
        loop = STTModule(lambda *_: None, channel="loop", loopback=True)
        self.assertFalse(mic._reject_text("Да."))
        self.assertFalse(mic._reject_text("Стоп!"))
        self.assertTrue(loop._reject_text("Да."))
        self.assertFalse(loop._reject_text("Да, конечно."))
        self.assertTrue(loop._reject_text("Спасибо за просмотр."))

    def test_loopback_device_resolution_prefers_exact_name_and_validates_index(self):
        devices = [
            {"index": 3, "name": "Speakers [Loopback]", "isLoopbackDevice": True,
             "maxInputChannels": 2},
            {"index": 7, "name": "Voicemeeter Input [Loopback]", "isLoopbackDevice": True,
             "maxInputChannels": 2},
        ]

        class FakePA:
            def get_device_info_generator(self):
                return iter(devices)

            def get_default_wasapi_loopback(self):
                return devices[0]

        module = STTModule(lambda *_: None, channel="loop", loopback=True,
                           device_name="Voicemeeter Input [Loopback]")
        self.assertEqual(7, module._resolve_loopback_device(FakePA())["index"])
        module.device_name = "99"
        with self.assertRaises(RuntimeError):
            module._resolve_loopback_device(FakePA())

    def test_transcribe_uses_channel_prompt_and_energy_vad_by_default(self):
        class Segment:
            text = " Нима, привет "

        class Info:
            language_probability = 1.0

        class FakeModel:
            def __init__(self):
                self.kwargs = None

            def transcribe(self, audio, **kwargs):
                self.kwargs = kwargs
                return iter([Segment()]), Info()

        mic_model = FakeModel()
        mic = STTModule(lambda *_: None, channel="mic", shared_model=mic_model)
        self.assertEqual("Нима, привет", mic._transcribe(np.zeros(1600)))
        self.assertEqual("ru", mic_model.kwargs["language"])
        self.assertFalse(mic_model.kwargs["vad_filter"])
        self.assertTrue(mic_model.kwargs["initial_prompt"])

        loop_model = FakeModel()
        loop = STTModule(lambda *_: None, channel="loop", loopback=True,
                         shared_model=loop_model)
        loop._transcribe(np.zeros(1600))
        self.assertIsNone(loop_model.kwargs["initial_prompt"])

    def test_audio_start_is_marked_only_when_playback_begins(self):
        recorder = TraceRecorder()
        tts = TTSModule(refs=[])
        temp = Path(tempfile.mkdtemp()) / "chunk.wav"
        temp.write_bytes(b"placeholder")
        tts._synthesize = lambda *args, **kwargs: temp

        def fake_play(path, stop_flag=None, on_audio_start=None):
            self.assertFalse(any(stage == "audio_start" for stage, _, _ in recorder.events))
            on_audio_start()
            return True

        tts._play = fake_play
        self.assertTrue(tts.speak_stream(iter(["Это достаточно длинная тестовая фраза."]),
                                         trace=recorder, request_id="rid-3"))
        stages = [stage for stage, rid, _ in recorder.events if rid == "rid-3"]
        self.assertIn("tts_synth_done", stages)
        self.assertIn("audio_start", stages)
        self.assertLess(stages.index("tts_synth_done"), stages.index("audio_start"))


if __name__ == "__main__":
    unittest.main()
