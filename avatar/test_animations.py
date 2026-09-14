"""Живой тест анимаций: аватар + команды как из debug menu."""
import time, sys
sys.path.insert(0, ".")
from avatar.bridge import AvatarBridge

b = AvatarBridge()
b.start()
time.sleep(8)  # загрузка модели

steps = [
    ("VRMA из песочницы (vrma_VRMA_01)", dict(action="vrma_VRMA_01")),
    ("семантическая dance", dict(action="dance")),
    ("повтор той же команды (seq)", dict(action="vrma_VRMA_01")),
    ("jump", dict(action="jump")),
    ("мусорное действие (не должно упасть)", dict(action="boredom_kick_pebbles")),
    ("idle", dict(action="idle")),
]
for label, kwargs in steps:
    print("->", label)
    b.command_avatar(**kwargs)
    time.sleep(6)
b.stop()
print("DONE")
