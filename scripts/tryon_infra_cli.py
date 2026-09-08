#!/usr/bin/env python3
"""Operator CLI for queue health, reconciliation, retention, and failure backfill.

The command-line half of what Worker Control shows: queue depth and provider circuit
state, the reconciliation audit for jobs whose publish/notify sequence half-applied,
and a backfill that fills in the failure taxonomy on older failed jobs.

Reconciliation is read-only — it reports findings and marks which are safe to replay,
it does not replay them. Usage examples: docs/TRYON_CRITICAL_INFRASTRUCTURE.md.

Retention (try-on#45): `prune-queue` trims the terminal done/failed buckets by age
and count; `sweep-processing` reconciles queue/processing workspaces against Atlas
job state and moves only the terminal ones. Both are dry-run unless --apply.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tryon_queue_worker import load_env_file
from services.mongo_uri import normalize_mongodb_uri
from services.worker_infra import (
    QueueBackpressurePolicy,
    ProviderCircuitBreaker,
    ProviderPolicy,
    classify_failure_category,
    failure_note,
    load_event_metrics,
    reconcile_jobs,
    summarize_queue,
)
from services.worker_runtime import get_worker_runtime_dir, read_recent_worker_events


def config() -> tuple[MongoClient, Any]:
    load_env_file(REPO_ROOT / ".env.tryon-worker")
    load_env_file(REPO_ROOT / ".env.local")
    uri = (os.getenv("MONGODB_ATLAS_URI") or os.getenv("MONGODB_URI") or "").strip()
    db_name = (os.getenv("MONGODB_DB_NAME") or os.getenv("MONGODB_DB") or "").strip()
    if not uri or not db_name:
        raise RuntimeError("MONGODB_ATLAS_URI/MONGODB_URI and MONGODB_DB_NAME/MONGODB_DB are required")
    client = MongoClient(normalize_mongodb_uri(uri), serverSelectionTimeoutMS=5000)
    return client, client[db_name]


def provider_policies() -> dict[str, ProviderPolicy]:
    return {
        "local": ProviderPolicy("local", int(os.getenv("TRYON_LOCAL_API_TIMEOUT_SECONDS") or 900), daily_request_limit=int(os.getenv("TRYON_LOCAL_DAILY_LIMIT") or 10000)),
        "segmind": ProviderPolicy("segmind", int(os.getenv("SEGMIND_API_TIMEOUT_SECONDS") or 180), daily_request_limit=int(os.getenv("SEGMIND_DAILY_LIMIT") or 500)),
        "fal": ProviderPolicy("fal", int(os.getenv("FAL_TRYON_TIMEOUT_SECONDS") or 300), daily_request_limit=int(os.getenv("FAL_DAILY_LIMIT") or 500)),
        "imgbb": ProviderPolicy("imgbb", 120, daily_request_limit=int(os.getenv("IMGBB_DAILY_LIMIT") or 2000)),
        "camera": ProviderPolicy("camera", 60, daily_request_limit=int(os.getenv("CAMERA_CALLBACK_DAILY_LIMIT") or 5000)),
    }


def cmd_status(_args: argparse.Namespace) -> int:
    client, db = config()
    try:
        policy = QueueBackpressurePolicy(
            enabled=(os.getenv("TRYON_BACKPRESSURE_ENABLED") or "true").lower() not in {"0", "false", "no"},
            max_ready_jobs=int(os.getenv("TRYON_BACKPRESSURE_MAX_READY_JOBS") or 50),
            max_oldest_ready_age_seconds=int(os.getenv("TRYON_BACKPRESSURE_MAX_OLDEST_READY_AGE_SECONDS") or 3600),
        )
        breaker = ProviderCircuitBreaker(get_worker_runtime_dir(REPO_ROOT) / "provider_metrics.json", provider_policies())
        payload = summarize_queue(db["tryon_jobs"], policy=policy)
        payload["providerScorecard"] = breaker.scorecard()
        payload["recentEventMetrics"] = load_event_metrics(read_recent_worker_events(limit=200, app_root=REPO_ROOT))
        print(json.dumps(payload, indent=2, default=str))
        return 0 if not payload["backpressure"]["active"] else 2
    finally:
        client.close()


def cmd_reconcile(args: argparse.Namespace) -> int:
    client, db = config()
    try:
        report = reconcile_jobs(db["tryon_jobs"], limit=args.limit)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["findingCount"] == 0 else 2
    finally:
        client.close()


def cmd_backfill_failure_notes(args: argparse.Namespace) -> int:
    client, db = config()
    try:
        updated = 0
        for doc in db["tryon_jobs"].find({"status": "failed", "$or": [{"error.category": {"$exists": False}}, {"error.category": None}, {"error.category": ""}]}, {"jobId": 1, "error": 1}).limit(args.limit):
            error = doc.get("error") or {}
            category = classify_failure_category(str(error.get("code") or ""), str(error.get("message") or ""))
            note = failure_note(category, error.get("message"))
            db["tryon_jobs"].update_one({"_id": doc["_id"]}, {"$set": {"error.category": category, "error.operatorNote": note}})
            updated += 1
        print(json.dumps({"updated": updated}, indent=2))
        return 0
    finally:
        client.close()


QUEUE_ROOT = Path(os.getenv("TRYON_QUEUE_ROOT") or (Path(__file__).resolve().parents[1] / "queue"))


def _should_prune(entry: Path, cutoff_epoch: float, index: int, keep: int) -> bool:
    """Prune predicate for a terminal (done/failed) workspace dir.

    A dir is prunable if it is OLDER than the age cutoff, OR it is beyond the
    keep-newest-N window. `index` is the position when dirs are sorted
    newest-first (0 = newest). Never called for queue/processing (that is swept
    separately with an Atlas terminal+lease check).
    """
    try:
        mtime = entry.stat().st_mtime
    except OSError:
        return False
    if index >= keep:
        return True
    return mtime < cutoff_epoch


def cmd_prune_queue(args: argparse.Namespace) -> int:
    """Prune queue/done and queue/failed workspaces by age and count.

    Filesystem-only and safe: done/failed jobs are already terminal in Atlas, so
    no live/leased job is ever touched (queue/processing is intentionally NOT
    pruned here - `sweep-processing` handles that bucket against Atlas state).
    Defaults to --dry-run so nothing is deleted without an explicit --apply.
    """
    import shutil

    cutoff = time.time() - (args.days * 86400)
    removed = {"done": 0, "failed": 0}
    freed_bytes = 0
    for bucket in ("done", "failed"):
        root = QUEUE_ROOT / bucket
        if not root.is_dir():
            continue
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.stat().st_mtime, reverse=True)
        for index, d in enumerate(dirs):
            if not _should_prune(d, cutoff, index, args.keep):
                continue
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            print(f"{'WOULD PRUNE' if not args.apply else 'PRUNED'} {bucket}/{d.name} ({size//1024} KB)")
            if args.apply:
                shutil.rmtree(d, ignore_errors=True)
            removed[bucket] += 1
            freed_bytes += size
    print(json.dumps({
        "mode": "apply" if args.apply else "dry-run",
        "keptNewest": args.keep, "olderThanDays": args.days,
        "removed": removed, "freedKB": freed_bytes // 1024,
    }, indent=2))
    return 0


ACTIVE_JOB_STATUSES = ("claimed", "processing", "uploading_result", "notifying_camera")

SWEEP_SKIP_IN_FLIGHT = "SKIP in-flight"
SWEEP_REPORT_NO_RECORD = "REPORT no Atlas record"
SWEEP_REPORT_STALE_LEASE = "REPORT stale-lease"
SWEEP_MOVE_DONE = "MOVE -> done"
SWEEP_MOVE_FAILED = "MOVE -> failed"


def _parse_iso(value: Any) -> float | None:
    """ISO-8601 (worker format, `Z` suffix) -> epoch seconds; None if unparseable."""
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def sweep_processing_verdict(job: dict[str, Any] | None, *, now_epoch: float, grace_seconds: float) -> str:
    """Decide what to do with one queue/processing/<jobId> workspace.

    Atlas is the source of truth for job STATE; the directory is only a
    workspace. The only two verdicts that touch the filesystem are the terminal
    ones (done -> queue/done, failed -> queue/failed). Everything else is
    reported and left in place:
    - no Atlas record: nothing to reconcile against, never touch.
    - active status with a live lease: a worker owns it.
    - active status with a lease expired past the grace window, or retry_wait:
      the worker's own recover_stale_jobs sweep re-queues these; moving the
      workspace out from under a requeue would lose the inputs.
    """
    if not job:
        return SWEEP_REPORT_NO_RECORD
    status = str(job.get("status") or "").strip().lower()
    if status == "done":
        return SWEEP_MOVE_DONE
    if status == "failed":
        return SWEEP_MOVE_FAILED
    if status == "retry_wait":
        return SWEEP_REPORT_STALE_LEASE
    if status in ACTIVE_JOB_STATUSES:
        lease_epoch = _parse_iso((job.get("processing") or {}).get("leaseExpiresAt"))
        if lease_epoch is None or lease_epoch + grace_seconds >= now_epoch:
            return SWEEP_SKIP_IN_FLIGHT
        return SWEEP_REPORT_STALE_LEASE
    return SWEEP_SKIP_IN_FLIGHT


def sweep_processing(processing_root: Path, jobs: Any, *, apply: bool, grace_minutes: int, now_epoch: float | None = None) -> list[dict[str, Any]]:
    """Walk queue/processing, decide per dir, move only terminal ones when `apply`.

    `jobs` needs only `find_one({"jobId": ...})`, so the smoke test can pass a
    dict-backed stub. Returns the verdict rows (also printed as a table).
    """
    import shutil

    now_epoch = time.time() if now_epoch is None else now_epoch
    rows: list[dict[str, Any]] = []
    if not processing_root.is_dir():
        return rows
    for entry in sorted(d for d in processing_root.iterdir() if d.is_dir()):
        job = jobs.find_one({"jobId": entry.name})
        verdict = sweep_processing_verdict(job, now_epoch=now_epoch, grace_seconds=grace_minutes * 60)
        status = str((job or {}).get("status") or "-")
        lease = str(((job or {}).get("processing") or {}).get("leaseExpiresAt") or "-")
        action = "reported"
        if verdict in (SWEEP_MOVE_DONE, SWEEP_MOVE_FAILED):
            bucket = "done" if verdict == SWEEP_MOVE_DONE else "failed"
            target = processing_root.parent / bucket / entry.name
            if apply:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    action = f"kept (target exists: {bucket}/{entry.name})"
                else:
                    shutil.move(str(entry), str(target))
                    action = f"moved -> {bucket}/{entry.name}"
            else:
                action = f"would move -> {bucket}/{entry.name}"
        elif verdict == SWEEP_SKIP_IN_FLIGHT:
            action = "skipped"
        rows.append({"dir": entry.name, "status": status, "leaseExpiresAt": lease, "verdict": verdict, "action": action})
    return rows


def _print_sweep_table(rows: list[dict[str, Any]], *, apply: bool) -> None:
    cols = ("dir", "status", "leaseExpiresAt", "verdict", "action")
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) if rows else len(c) for c in cols}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))
    print(json.dumps({
        "mode": "apply" if apply else "dry-run",
        "dirs": len(rows),
        "verdicts": {v: sum(1 for r in rows if r["verdict"] == v) for v in sorted({r["verdict"] for r in rows})},
    }, indent=2))


def cmd_sweep_processing(args: argparse.Namespace) -> int:
    """Reconcile queue/processing workspaces against Atlas and move only terminal ones."""
    client, db = config()
    try:
        rows = sweep_processing(QUEUE_ROOT / "processing", db["tryon_jobs"], apply=args.apply, grace_minutes=args.grace_minutes)
        _print_sweep_table(rows, apply=args.apply)
        return 0
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Try-on critical infrastructure CLI")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="Print queue pressure, provider scorecard, and event metrics").set_defaults(func=cmd_status)
    reconcile = sub.add_parser("reconcile", help="Audit Atlas job consistency")
    reconcile.add_argument("--limit", type=int, default=200)
    reconcile.set_defaults(func=cmd_reconcile)
    backfill = sub.add_parser("backfill-failure-notes", help="Add normalized failure taxonomy to failed jobs")
    backfill.add_argument("--limit", type=int, default=500)
    backfill.set_defaults(func=cmd_backfill_failure_notes)
    prune = sub.add_parser("prune-queue", help="Prune terminal queue/done and queue/failed workspaces by age+count")
    prune.add_argument("--days", type=int, default=30, help="prune dirs older than this many days (default 30)")
    prune.add_argument("--keep", type=int, default=200, help="always keep the newest N per bucket (default 200)")
    prune.add_argument("--apply", action="store_true", help="actually delete (default is dry-run)")
    prune.set_defaults(func=cmd_prune_queue)
    sweep = sub.add_parser("sweep-processing", help="Reconcile queue/processing dirs against Atlas; move done/failed, report the rest")
    sweep.add_argument("--grace-minutes", type=int, default=60, help="an expired lease is only called stale after this many minutes (default 60)")
    sweep.add_argument("--apply", action="store_true", help="actually move terminal dirs (default is dry-run)")
    sweep.set_defaults(func=cmd_sweep_processing)
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
