"""tests/test_conversation.py — реальная беседа с Нимфей через настоящий пайплайн
(TTS/avatar — фейковые). Запуск:
    myenv\\Scripts\\python.exe tests\\test_conversation.py

Проверяет связность, контекст и память на живой модели Ollama.
"""
import sys, time, tempfile, json
from pathlib import Path
sys.path.insert(0, ".")
from memory.memory_module import MemoryModule
from prompts.prompt_builder import PromptBuilder
from llm.llm_module import LLMModule
from pipeline.pipeline import DialoguePipeline

tmp = Path(tempfile.mkdtemp())
mem = MemoryModule(str(tmp / "memory.json"))
prompts = PromptBuilder(mem)
llm = LLMModule()

class FakeTTS:
    speaking = False
    def stop(self): pass
    def speak_stream(self, sentences, trace=None, request_id=None, mood_box=None):
        for s in sentences:
            tag = f" [{mood_box[0]}]" if mood_box else ""
            print(f"    [TTS]{tag} {s}")

class FakeAvatar:
    def command_avatar(self, **kw): pass
    def set_mouth(self, v): pass

pipe = DialoguePipeline(mem, prompts, llm, FakeTTS(), FakeAvatar())
pipe._run_async = lambda fn, *a: fn(*a)  # синхронно для теста

TURNS = [
    "Нима, привет! Как дела?",
    "Нима, меня зовут Кизил, я по вечерам стримю доту",
    "Нима, а как меня зовут, помнишь?",
    "Нима, а во что я стримлю по вечерам?",
    "Нима, пошли в танцы",
    "Нима, а что ты думаешь про квазары?",
]
for t in TURNS:
    print(f"\n>>> {t}")
    t0 = time.time()
    pipe.handle_user_text(t, source="manual")
    print(f"    ({time.time()-t0:.1f}s)")
time.sleep(0.5)
print("\n--- последние записи памяти ---")
for it in mem.get_recent_dialog(limit=12):
    print(f"  [{it['role']}] {it['content'][:90]}")
