#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model_paths import get_models_root
from services.model_sync import plan_sync, sync_profile, write_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Synchronize shared-model assets into the canonical vault.")
    parser.add_argument("--profile", choices=("core", "optional", "all"), default="core")
    parser.add_argument("--plan", action="store_true", help="Print the sync plan without downloading.")
    parser.add_argument("--write-manifest", action="store_true", help="Refresh manifest.json after sync.")
    args = parser.parse_args()

    models_root = get_models_root()
    if args.plan:
        print(json.dumps(plan_sync(models_root, args.profile), indent=2, sort_keys=True))
        return 0

    result = sync_profile(models_root, args.profile)
    print(json.dumps(result["report"], indent=2, sort_keys=True))
    if args.write_manifest:
        target = write_manifest(models_root, result["report"])
        print(f"Wrote manifest: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
