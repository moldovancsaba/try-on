"""Sidecar metadata written next to each generated image.

Records what produced a render — parameters, quality verdict, and the capability state
at the time — as `<output>.png.json`. The point is after-the-fact answerability: when
someone asks why one output looks different, the sidecar says which seed, sampler, and
model readiness produced it, without needing the job to still exist in Atlas.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def build_output_metadata(
    *,
    feature_key: str,
    output_path: Path,
    parameters: dict[str, Any],
    quality_validation: dict[str, Any],
    capability_report: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "feature": feature_key,
        "output_path": str(output_path),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "parameters": parameters,
        "quality_validation": quality_validation,
        "capabilities": capability_report["features"].get(feature_key),
        "extra": extra or {},
    }


def write_sidecar_metadata(output_path: Path, metadata: dict[str, Any]) -> Path:
    """Write `metadata` beside the output and return the sidecar path.

    The suffix is appended rather than replaced — `render.png` gets
    `render.png.json` — so the sidecar can never collide with a differently
    formatted render of the same name. Overwrites silently.
    """
    output_path = Path(output_path)
    sidecar_path = output_path.with_suffix(f"{output_path.suffix}.json")
    with sidecar_path.open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return sidecar_path

