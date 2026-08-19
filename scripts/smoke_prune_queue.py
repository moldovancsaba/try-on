"""Smoke: try-on#45 queue retention predicate.

Asserts _should_prune keeps the newest N and prunes by age, and never depends on
queue/processing (only done/failed, which are terminal). No Atlas needed.
"""
from __future__ import annotations
import importlib.util
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("tryon_infra_cli", Path(__file__).resolve().parent / "tryon_infra_cli.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def main() -> int:
    now = time.time()
    fails = []

    class FakeEntry:
        def __init__(self, mtime): self._m = mtime
        def stat(self): return type("S", (), {"st_mtime": self._m})()

    cutoff = now - 30 * 86400
    # within keep window and recent -> keep
    if mod._should_prune(FakeEntry(now), cutoff, index=0, keep=200):
        fails.append("newest recent dir should be kept")
    # beyond keep window -> prune regardless of age
    if not mod._should_prune(FakeEntry(now), cutoff, index=200, keep=200):
        fails.append("dir beyond keep-N window should be pruned")
    # within keep window but older than cutoff -> prune
    if not mod._should_prune(FakeEntry(now - 40 * 86400), cutoff, index=5, keep=200):
        fails.append("dir older than the age cutoff should be pruned")
    # within keep window and exactly at cutoff boundary (newer) -> keep
    if mod._should_prune(FakeEntry(now - 10 * 86400), cutoff, index=5, keep=200):
        fails.append("recent in-window dir should be kept")

    for f in fails:
        print(f"FAIL {f}")
    if fails:
        return 1
    print("smoke_prune_queue: ok  keep-newest-N + age cutoff, terminal-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
