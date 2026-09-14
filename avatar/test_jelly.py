"""Живой тест желейной физики груди: загрузка модели + движения, проверка лога.

Запуск: myenv\\Scripts\\python.exe avatar\\test_jelly.py
Логи рендерера — avatar/renderer_error.log: ищем «[nima] jelly:».
"""
import os
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["NIMA_DEBUG_QUERY"] = "jellylog&opaque=1"

from avatar.bridge import AvatarBridge  # noqa: E402

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renderer_error.log")
open(LOG, "w", encoding="utf-8").close()  # чистый лог

b = AvatarBridge()
b.start()
time.sleep(9)  # загрузка модели + патч материалов

steps = [
    ("идл (покачивание)", None, 4),
    ("dance (VRMA_01)", dict(action="vrma_VRMA_01"), 8),
    ("jump (VRMA_02)", dict(action="vrma_VRMA_02"), 7),
    ("spinning (VRMA_04)", dict(action="vrma_VRMA_04"), 8),
]
for label, kwargs, wait in steps:
    print("->", label)
    if kwargs:
        b.command_avatar(**kwargs)
    time.sleep(wait)
b.stop()
time.sleep(1)

text = open(LOG, encoding="utf-8", errors="replace").read()
setup = [l for l in text.splitlines() if "jelly: патчу" in l]
offsets = [l for l in text.splitlines() if "jelly: offset" in l]
errors = [l for l in text.splitlines() if "Shader Error" in l or "console[3]" in l]

print("--- setup:", setup[0][:220] if setup else "НЕТ ЛОГА ПАТЧА")
mag, sides = [], set()
for l in offsets:
    for name in ("offsetL", "offsetR"):
        if f"{name} = |" in l:
            sides.add(name)
            try:
                mag.append(float(l.split(f"{name} = |")[1].split(" ")[0]))
            except Exception:
                pass
if mag:
    print(f"— замеров: {len(offsets)}, стороны: {sorted(sides)}, max |offset| = {max(mag):.4f}")
else:
    print("— offset НЕ ДВИГАЕТСЯ")
print("--- шейдерные ошибки:", "ЕСТЬ: " + errors[0][:200] if errors else "нет")
ok = bool(setup) and bool(mag) and len(sides) == 2 and max(mag) > 0.002 and not errors
print("RESULT:", "OK" if ok else "FAIL")
sys.exit(0 if ok else 1)
