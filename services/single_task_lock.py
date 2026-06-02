from __future__ import annotations

import fcntl
import os
from pathlib import Path

from model_paths import get_app_root


class SingleTaskLock:
    """File-backed lock for local single-task service work."""

    def __init__(self, name: str, *, app_root: Path | None = None):
        root = app_root or get_app_root()
        self.path = root / ".runtime" / "locks" / f"{name}.lock"
        self._handle = None

    def acquire(self, *, blocking: bool = False) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            handle.close()
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> SingleTaskLock:
        self.acquire(blocking=True)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
