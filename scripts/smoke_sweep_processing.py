"""Smoke: try-on#45 queue/processing sweep safety.

Asserts, against a temp queue tree and a dict-backed stand-in for the Atlas
`tryon_jobs` collection, that sweep-processing:
- never moves a leased in-flight job,
- never moves an active job whose lease expired (the worker's reaper owns it),
- moves a `done` job to queue/done,
- leaves a dir with no Atlas record untouched.
No Atlas, no pytest: plain python, exit non-zero on failure.
"""
from __future__ import annotations
import importlib.util
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("tryon_infra_cli", Path(__file__).resolve().parent / "tryon_infra_cli.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


class StubJobs:
    """The one method sweep_processing needs from a pymongo collection."""

    def __init__(self, docs: dict[str, dict]):
        self._docs = docs

    def find_one(self, query: dict):
        return self._docs.get(query.get("jobId"))


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def main() -> int:
    fails: list[str] = []
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    now_epoch = now.timestamp()
    jobs = StubJobs({
        "job_inflight": {"status": "processing", "processing": {"leaseExpiresAt": _iso(now + timedelta(minutes=5))}},
        "job_expired": {"status": "processing", "processing": {"leaseExpiresAt": _iso(now - timedelta(hours=3))}},
        "job_done": {"status": "done", "processing": {"leaseExpiresAt": None}},
        "job_failed": {"status": "failed", "processing": {"leaseExpiresAt": None}},
        "job_retry": {"status": "retry_wait", "processing": {"leaseExpiresAt": None}},
    })

    with tempfile.TemporaryDirectory() as tmp:
        queue = Path(tmp) / "queue"
        processing = queue / "processing"
        for name in ("job_inflight", "job_expired", "job_done", "job_failed", "job_retry", "job_norecord"):
            (processing / name).mkdir(parents=True)
            (processing / name / "metadata.json").write_text("{}")

        # dry-run: nothing moves, verdicts are right
        rows = {r["dir"]: r for r in mod.sweep_processing(processing, jobs, apply=False, grace_minutes=60, now_epoch=now_epoch)}
        expected = {
            "job_inflight": mod.SWEEP_SKIP_IN_FLIGHT,
            "job_expired": mod.SWEEP_REPORT_STALE_LEASE,
            "job_done": mod.SWEEP_MOVE_DONE,
            "job_failed": mod.SWEEP_MOVE_FAILED,
            "job_retry": mod.SWEEP_REPORT_STALE_LEASE,
            "job_norecord": mod.SWEEP_REPORT_NO_RECORD,
        }
        for name, verdict in expected.items():
            if rows.get(name, {}).get("verdict") != verdict:
                fails.append(f"dry-run verdict for {name}: expected {verdict!r}, got {rows.get(name, {}).get('verdict')!r}")
        for name in expected:
            if not (processing / name).is_dir():
                fails.append(f"dry-run must not move {name}")

        # apply: only done/failed move; the rest stay exactly where they were
        mod.sweep_processing(processing, jobs, apply=True, grace_minutes=60, now_epoch=now_epoch)
        for name in ("job_inflight", "job_expired", "job_retry", "job_norecord"):
            if not (processing / name / "metadata.json").is_file():
                fails.append(f"apply must leave {name} in queue/processing")
        if (processing / "job_done").exists() or not (queue / "done" / "job_done" / "metadata.json").is_file():
            fails.append("apply must move job_done to queue/done")
        if (processing / "job_failed").exists() or not (queue / "failed" / "job_failed" / "metadata.json").is_file():
            fails.append("apply must move job_failed to queue/failed")

        # an expired lease still inside the grace window counts as in flight
        recent = StubJobs({"job_recent": {"status": "claimed", "processing": {"leaseExpiresAt": _iso(now - timedelta(minutes=10))}}})
        (processing / "job_recent").mkdir()
        verdict = mod.sweep_processing(processing, recent, apply=True, grace_minutes=60, now_epoch=now_epoch)
        got = {r["dir"]: r["verdict"] for r in verdict}.get("job_recent")
        if got != mod.SWEEP_SKIP_IN_FLIGHT or not (processing / "job_recent").is_dir():
            fails.append(f"lease expired 10 min ago with 60 min grace must be SKIP in-flight, got {got!r}")

    for f in fails:
        print(f"FAIL {f}")
    if fails:
        return 1
    print("smoke_sweep_processing: ok  in-flight kept, expired-lease kept, done moved, failed moved, no-record kept")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
