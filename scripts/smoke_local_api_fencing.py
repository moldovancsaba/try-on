"""Smoke: try-on#42 API fencing (source-level).

Asserts the origin-guard middleware and the render-path containment exist. Live
403/400 behavior is checked separately against the running server after restart.
"""
from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    src = (ROOT / "app.py").read_text()
    fails = []
    if "_origin_guard" not in src or "forbidden origin" not in src:
        fails.append("origin-guard middleware missing")
    if "must be within the try-on workspace" not in src:
        fails.append("render-path containment missing")
    if 'version="12.2.0"' not in src:
        fails.append("fleet version 12.2.0 not set on the app")
    # the containment must resolve against the project root, not a user string
    if "_project_root = Path(__file__).resolve().parent" not in src:
        fails.append("path containment does not anchor to the project root")
    for f in fails:
        print(f"FAIL {f}")
    if fails:
        return 1
    print("smoke_local_api_fencing: ok  origin guard, path containment, version 12.2.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
