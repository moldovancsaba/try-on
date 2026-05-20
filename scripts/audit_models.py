#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


DEFAULT_MODELS_ROOT = Path("/Users/Shared/Models")


def get_models_root(cli_value: str | None) -> Path:
    raw = cli_value or os.environ.get("TRYON_MODELS_ROOT") or str(DEFAULT_MODELS_ROOT)
    return Path(raw).expanduser().resolve()


def get_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_symlink():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        except FileNotFoundError:
            continue
    return total


def build_manifest(models_root: Path) -> dict:
    entries = []
    for child in sorted(models_root.iterdir(), key=lambda p: p.name.lower()):
        entries.append(
            {
                "name": child.name,
                "path": str(child),
                "type": "symlink" if child.is_symlink() else "dir" if child.is_dir() else "file",
                "size_bytes": get_size_bytes(child),
            }
        )

    required_paths = [
        "checkpoints/sd15-inpainting",
        "checkpoints/stable-video-diffusion-img2vid-xt",
        "processors/catvton-segmentation",
        "processors/face-restoration",
        "vae/sd15-vae-ft-mse",
    ]
    return {
        "models_root": str(models_root),
        "entries": entries,
        "required_status": {
            rel: (models_root / rel).exists() for rel in required_paths
        },
        "warnings": collect_warnings(models_root),
    }


def collect_warnings(models_root: Path) -> list[str]:
    warnings: list[str] = []
    llm_path = models_root / "LLM"
    if llm_path.exists() and not llm_path.is_symlink():
        warnings.append("Legacy uppercase namespace detected: LLM")
    if (models_root / "settings.json").exists():
        warnings.append("Legacy app settings file detected in model vault: settings.json")
    if not (models_root / ".cache" / "huggingface").exists():
        warnings.append("Missing shared Hugging Face cache directory: .cache/huggingface")
    return warnings


def write_manifest(models_root: Path, manifest: dict) -> Path:
    target = models_root / "manifest.json"
    with target.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return target


def migrate_florence_namespace(models_root: Path, *, dry_run: bool) -> list[str]:
    notes: list[str] = []
    source_root = models_root / "LLM"
    source = source_root / "Florence-2-base"
    target = models_root / "llms" / "vision" / "florence-2-base"

    if not source.exists():
        notes.append("No legacy Florence model found under LLM/Florence-2-base.")
        return notes

    if target.exists():
        notes.append(f"Target already exists: {target}")
        return notes

    notes.append(f"Plan Florence migration: {source} -> {target}")
    if dry_run:
        return notes

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))
    if source_root.exists():
        for child in source_root.iterdir():
            if child.is_symlink() or child.is_file():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child)
        source_root.rmdir()
    source_root.symlink_to(target.parent)
    notes.append(f"Created compatibility symlink: {source_root} -> {target.parent}")
    return notes


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit and normalize the shared model vault.")
    parser.add_argument("--models-root", help="Override TRYON_MODELS_ROOT for this run.")
    parser.add_argument("--write-manifest", action="store_true", help="Write manifest.json into the model vault.")
    parser.add_argument("--migrate-florence", action="store_true", help="Move LLM/Florence-2-base into llms/vision with a compatibility symlink.")
    parser.add_argument("--dry-run", action="store_true", help="Print intended mutations without changing the filesystem.")
    args = parser.parse_args()

    models_root = get_models_root(args.models_root)
    manifest = build_manifest(models_root)
    print(json.dumps(manifest, indent=2, sort_keys=True))

    if args.write_manifest:
        target = models_root / "manifest.json"
        if args.dry_run:
            print(f"DRY RUN: would write manifest to {target}")
        else:
            written = write_manifest(models_root, manifest)
            print(f"Wrote manifest: {written}")

    if args.migrate_florence:
        for note in migrate_florence_namespace(models_root, dry_run=args.dry_run):
            print(note)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
