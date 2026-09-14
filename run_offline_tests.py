from __future__ import annotations

import time

from debug_menu.full_test import TESTS

started = time.perf_counter()
failures = []
print(f"TOTAL {len(TESTS)}")
for category, name, fn in TESTS:
    try:
        fn()
        print(f"PASS {category}: {name}")
    except Exception as exc:
        failures.append((category, name, repr(exc)))
        print(f"FAIL {category}: {name} — {exc!r}")
print(
    f"SUMMARY {len(TESTS) - len(failures)} passed, {len(failures)} failed, "
    f"elapsed {time.perf_counter() - started:.3f}s"
)
raise SystemExit(1 if failures else 0)
