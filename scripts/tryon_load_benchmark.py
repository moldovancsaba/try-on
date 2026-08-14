#!/usr/bin/env python3
"""Print a dry-run throughput plan for a given job count. Renders nothing.

Estimates how long a backlog would take on a single-slot worker at the configured
poll interval. It is arithmetic for capacity planning, not a load test — nothing is
submitted and no provider is called.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from services.worker_infra import INFRA_CONTRACT_VERSION, now_iso


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run queue throughput benchmark plan")
    parser.add_argument("--jobs", type=int, default=20)
    parser.add_argument("--median-seconds", type=float, default=180.0)
    parser.add_argument("--output", default=".runtime/load_benchmark_plan.json")
    args = parser.parse_args()
    started = time.time()
    projected_seconds = max(0, args.jobs) * max(1.0, args.median_seconds)
    payload = {
        "schemaVersion": INFRA_CONTRACT_VERSION,
        "generatedAt": now_iso(),
        "mode": "dry_run_plan",
        "jobs": args.jobs,
        "assumedMedianJobSeconds": args.median_seconds,
        "projectedSingleWorkerSeconds": projected_seconds,
        "projectedSingleWorkerMinutes": round(projected_seconds / 60, 2),
        "acceptanceThresholds": {
            "noDuplicateClaim": True,
            "failedJobCategoryRequired": True,
            "providerMetricsRequired": True,
            "queueBackpressureVisible": True,
        },
        "elapsedPlanningSeconds": round(time.time() - started, 3),
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
