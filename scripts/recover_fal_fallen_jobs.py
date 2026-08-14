#!/usr/bin/env python3
"""One-off recovery: requeue fal jobs that failed from transient output-handling bugs.

Written for a specific incident — fal renders that succeeded upstream but were left
failed or retry_wait locally because the response was mishandled. It selects only that
signature, so it is safe to leave in the tree, but check --dry-run output before
running it: requeuing a job that genuinely failed costs another provider call.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from pymongo import MongoClient
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))
from tryon_queue_worker import load_config, now_iso


def load_jobs_collection(config: Any):
    client = MongoClient(config.mongodb_uri)
    return client, client[config.mongodb_db_name]["tryon_jobs"]


def make_filter():
    return {
        "status": {"$in": ["failed", "retry_wait"]},
        "$or": [
            {"processing.resolvedSetupProfile": "fal_tryon"},
            {"request.processingProfile": "fal_tryon"},
            {"request.setupId": "fal_ai_tryon"},
            {"processing.resolvedSetupId": "fal_ai_tryon"},
        ],
        "error": {"$exists": True},
        "$and": [
            {
                "$or": [
                    {"error.code": {"$in": ["processing_failed", "fal_status_failed", "fal_output_fetch_failed"]}},
                    {
                        "error.message": {
                            "$regex": r"401 Client Error: Unauthorized for url: https://queue\\.fal\\.run",
                            "$options": "i",
                        }
                    },
                    {"error.message": {"$regex": r"fal_", "$options": "i"}},
                ]
            }
        ],
    }


def recover_jobs(args: Any) -> None:
    config = load_config()
    client, collection = load_jobs_collection(config)
    try:
        selector = make_filter()
        jobs = list(collection.find(selector))
        print(f"found {len(jobs)} fal jobs in failed/retry_wait state to requeue")
        if not jobs:
            return

        for job in jobs:
            print(
                f" - {job.get('jobId')}: status={job.get('status')} attempt={job.get('processing', {}).get('attemptCount')} "
                f"error={job.get('error', {}).get('message')}"
            )

        if args.dry_run:
            print("dry-run mode enabled; no updates were made")
            return

        now = now_iso()
        update = collection.update_many(
            selector,
            {
                "$set": {
                    "status": "queued",
                    "stage": "queued",
                    "updatedAt": now,
                    "error": {"code": None, "message": None, "details": None},
                    "processing.lastError": None,
                },
                "$unset": {
                    "processing.nextAttemptAt": "",
                    "processing.leaseExpiresAt": "",
                },
            },
        )
        print(f"updated {update.modified_count} jobs to queued")
    finally:
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Requeue fal_tryon jobs that are failed/retry_wait due transient output handling bugs.")
    parser.add_argument("--dry-run", action="store_true", help="Show candidate jobs without modifying the queue.")
    args = parser.parse_args()
    recover_jobs(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
