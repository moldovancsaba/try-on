from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


QUALITY_CONTRACTS: dict[str, dict[str, Any]] = {
    "try_on": {
        "label": "Try-On",
        "min_width": 512,
        "min_height": 512,
        "min_mean_luma": 2.0,
        "min_std_luma": 4.0,
        "mask_coverage_range": (0.01, 0.95),
    },
}


def get_quality_contracts() -> dict[str, dict[str, Any]]:
    return QUALITY_CONTRACTS


def validate_image_output(feature_key: str, image, *, mask=None) -> dict[str, Any]:
    from PIL import Image

    contract = QUALITY_CONTRACTS[feature_key]
    pil_image = image if isinstance(image, Image.Image) else Image.fromarray(np.asarray(image))
    rgb = np.asarray(pil_image.convert("RGB"), dtype=np.float32)
    gray = rgb.mean(axis=2)
    metrics = {
        "width": int(pil_image.width),
        "height": int(pil_image.height),
        "mean_luma": float(gray.mean()),
        "std_luma": float(gray.std()),
    }

    failures: list[str] = []
    warnings: list[str] = []
    if pil_image.width < int(contract["min_width"]) or pil_image.height < int(contract["min_height"]):
        failures.append(
            f"Output resolution {pil_image.width}x{pil_image.height} is below the minimum contract."
        )
    if metrics["mean_luma"] < float(contract["min_mean_luma"]):
        failures.append("Output is near-black.")
    if metrics["std_luma"] < float(contract["min_std_luma"]):
        warnings.append("Output has very low tonal variance.")

    if mask is not None and "mask_coverage_range" in contract:
        mask_arr = np.asarray(mask.convert("L") if hasattr(mask, "convert") else mask, dtype=np.float32)
        coverage = float((mask_arr > 0).mean())
        metrics["mask_coverage"] = coverage
        min_cov, max_cov = contract["mask_coverage_range"]
        if coverage < min_cov or coverage > max_cov:
            warnings.append(
                f"Mask coverage {coverage:.3f} is outside the expected range {min_cov:.2f}-{max_cov:.2f}."
            )

    return {
        "feature": feature_key,
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics": metrics,
    }


def validate_video_output(feature_key: str, output_path: Path) -> dict[str, Any]:
    contract = QUALITY_CONTRACTS[feature_key]
    output_path = Path(output_path)
    failures: list[str] = []
    warnings: list[str] = []

    if not output_path.exists():
        failures.append(f"Missing output file: {output_path}")
    else:
        if output_path.suffix.lower() != contract["file_suffix"]:
            failures.append(f"Expected {contract['file_suffix']} output, got {output_path.suffix}")
        size = output_path.stat().st_size
        if size < int(contract["min_file_size_bytes"]):
            failures.append(f"Output video is unexpectedly small: {size} bytes")

    return {
        "feature": feature_key,
        "passed": not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics": {
            "exists": output_path.exists(),
            "suffix": output_path.suffix.lower(),
            "file_size_bytes": output_path.stat().st_size if output_path.exists() else 0,
        },
    }
