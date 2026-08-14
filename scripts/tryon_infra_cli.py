#!/usr/bin/env python3
"""Operator CLI for queue health, reconciliation, and failure backfill.

The command-line half of what Worker Control shows: queue depth and provider circuit
state, the reconciliation audit for jobs whose publish/notify sequence half-applied,
and a backfill that fills in the failure taxonomy on older failed jobs.

Reconciliation is read-only — it reports findings and marks which are safe to replay,
it does not replay them. Usage examples: docs/TRYON_CRITICAL_INFRASTRUCTURE.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
