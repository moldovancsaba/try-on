"""A/B render harness for the expose_arms mask mode (try-on#38).

Renders the SAME (person, garment) pair twice against the local running
try-on API - once with mask_mode=default, once with mask_mode=expose_arms -
and writes both outputs side by side (plus their masks) into an output
directory, with wall-clock timings, so a human can judge bare-arm quality
per the issue's verification matrix. This is the merge-gate tool: run it on
each matrix row's real photo and attach the outputs to the PR.

Usage:
  .venv311/bin/python scripts/ab_render_expose_arms.py \
      --person /path/to/person.jpg --garment /path/to/sleeveless_jersey.png \
      [--out-dir .runtime/ab_expose_arms] [--api http://127.0.0.1:7860/api/tryon/run]

Requires the local app server to be running and its models loaded.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import requests


def render(api: str, person: Path, garment: Path, out: Path, mask_mode: str) -> dict:
    payload = {
        "person_image_path": str(person.resolve()),
        "garment_image_path": str(garment.resolve()),
        "output_image_path": str(out.resolve()),
        "category": "upper",
        "category_source": "garment_type",  # keep the profile from stomping category
        "mask_mode": mask_mode,
        "sleeve_length": "default",
        "show_mask": True,
    }
    started = time.monotonic()
    response = requests.post(api, json=payload, timeout=1800)
    elapsed = time.monotonic() - started
    if response.status_code >= 400:
        raise SystemExit(f"{mask_mode}: API failed {response.status_code}: {response.text[:300]}")
    body = response.json()
    body["_elapsed_seconds"] = round(elapsed, 1)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--person", required=True, type=Path)
    parser.add_argument("--garment", required=True, type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path(".runtime/ab_expose_arms"))
    parser.add_argument("--api", default="http://127.0.0.1:7860/api/tryon/run")
    args = parser.parse_args()

    if not args.person.is_file():
        raise SystemExit(f"person image not found: {args.person}")
    if not args.garment.is_file():
        raise SystemExit(f"garment image not found: {args.garment}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out_dir = args.out_dir / f"{stamp}_{args.person.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {}
    for mode in ("default", "expose_arms"):
        out = out_dir / f"{mode}.png"
        print(f"[ab] rendering mask_mode={mode} ...", flush=True)
        results[mode] = render(args.api, args.person, args.garment, out, mode)
        print(f"[ab] {mode}: {results[mode]['_elapsed_seconds']}s -> {out}", flush=True)

    default_s = results["default"]["_elapsed_seconds"]
    expose_s = results["expose_arms"]["_elapsed_seconds"]
    delta_pct = ((expose_s - default_s) / default_s * 100) if default_s else 0.0
    summary = {
        "person": str(args.person),
        "garment": str(args.garment),
        "outputs": {m: str(out_dir / f"{m}.png") for m in results},
        "timings_seconds": {m: results[m]["_elapsed_seconds"] for m in results},
        "expose_arms_render_time_delta_pct": round(delta_pct, 1),
        # try-on#38 section 16: a >20% regression on the same pair is flagged.
        "render_time_within_bound": abs(delta_pct) <= 20.0,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if not summary["render_time_within_bound"]:
        print("[ab] WARNING: render-time delta exceeds the 20% bound - investigate before merge.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
