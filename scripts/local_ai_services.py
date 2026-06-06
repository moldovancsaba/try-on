#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from model_paths import get_models_root
from services.local_ai_services import (
    evaluate_model_packs,
    export_report_csv,
    run_local_ai_service,
    service_registry,
)


def _load_payload(value: str | None) -> dict:
    if not value:
        return {}
    candidate = Path(value)
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AI zero-external-cost service CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List local AI services and availability")
    sub.add_parser("model-packs", help="Verify local model pack readiness")

    run = sub.add_parser("run", help="Run a local AI service")
    run.add_argument("service_id")
    run.add_argument("--payload", help="JSON payload or path to JSON file")

    fixtures = sub.add_parser("fixtures", help="Generate synthetic local regression fixtures")
    fixtures.add_argument("--payload", help="Optional JSON payload or path")

    report = sub.add_parser("report", help="Export local AI service report CSV")
    report.add_argument("--output", default=".runtime/local_ai/reports/local_ai_services.csv")

    args = parser.parse_args()

    if args.command == "list":
        print(json.dumps(service_registry(get_models_root()), indent=2))
        return 0
    if args.command == "model-packs":
        print(json.dumps(evaluate_model_packs(get_models_root()), indent=2))
        return 0
    if args.command == "run":
        print(json.dumps(run_local_ai_service(REPO_ROOT, args.service_id, _load_payload(args.payload)), indent=2))
        return 0
    if args.command == "fixtures":
        print(json.dumps(run_local_ai_service(REPO_ROOT, "synthetic_fixture_generator", _load_payload(args.payload)), indent=2))
        return 0
    if args.command == "report":
        path = export_report_csv(REPO_ROOT, Path(args.output))
        print(json.dumps({"path": str(path)}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
