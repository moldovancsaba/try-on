from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PACKAGE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GarmentPackage:
    name: str
    package_dir: Path
    garment_path: Path
    metadata: dict[str, Any]
    package: dict[str, Any]


def safe_package_name(value: str) -> str:
    """Return `value` as a single safe path segment, or raise ValueError.

    Package names arrive from HTTP requests and are turned into filesystem paths, so
    anything with a separator, or `.`/`..`, is rejected rather than sanitized — a
    silently rewritten name would still read a file the caller did not name.
    """
    cleaned = value.strip().strip("/\\")
    if not cleaned or cleaned in {".", ".."} or Path(cleaned).name != cleaned:
        raise ValueError("invalid_package_name")
    return cleaned


def package_dir(packages_root: Path, name: str) -> Path:
    return packages_root / safe_package_name(name)


def load_garment_package(packages_root: Path, name: str) -> GarmentPackage:
    """Load a garment package by name, raising rather than returning a partial one.

    Second containment check after safe_package_name: the resolved directory must sit
    under the resolved packages_root, which is what stops a symlinked package from
    reading outside the tree.

    Raises FileNotFoundError naming the missing member (metadata.json, package.json,
    garment.png — all three are required), or ValueError for a bad path, an
    unsupported schemaVersion, or non-list keypoints. Error strings are stable codes,
    not prose: callers surface them to the API.
    """
    root = package_dir(packages_root, name).resolve()
    packages_root = packages_root.resolve()
    if root != packages_root and packages_root not in root.parents:
        raise ValueError("invalid_package_path")
    metadata_path = root / "metadata.json"
    package_path = root / "package.json"
    garment_path = root / "garment.png"
    if not metadata_path.exists():
        raise FileNotFoundError("missing_metadata_json")
    if not package_path.exists():
        raise FileNotFoundError("missing_package_json")
    if not garment_path.exists():
        raise FileNotFoundError("missing_garment_png")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    package = json.loads(package_path.read_text(encoding="utf-8"))
    schema_version = int(metadata.get("schemaVersion") or package.get("schemaVersion") or PACKAGE_SCHEMA_VERSION)
    if schema_version != PACKAGE_SCHEMA_VERSION:
        raise ValueError("unsupported_package_schema_version")
    if not isinstance(metadata.get("keypoints", []), list):
        raise ValueError("invalid_keypoints")

    return GarmentPackage(
        name=safe_package_name(name),
        package_dir=root,
        garment_path=garment_path,
        metadata=metadata,
        package=package,
    )
