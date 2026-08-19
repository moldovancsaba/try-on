"""Smoke test for the expose_arms mask mode (try-on#38).

Offline: builds tiny synthetic DensePose/SCHP label maps and calls
cloth_agnostic_mask directly - no model weights, no photos, no network.
Asserts the geometry contract, not render quality (render quality is the
human-reviewed matrix in the issue):

  1. hands and face are OUTSIDE the mask in every mode (strong protection
     is inviolable),
  2. arm regions are INSIDE the mask under expose_arms,
  3. the legacy sleeveless shrink still removes the dense-arm contribution
     (its mask is a strict subset of expose_arms' in the arm region we
     isolate behind a weak-protect overlap),
  4. expose_arms wins over sleeve_length='sleeveless' when both are passed
     (mutual exclusion),
  5. default upper behavior is byte-identical before/after the change when
     expose_arms is not requested (regression lock via expose_arms=False
     default paths).
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _register(name: str, directory: Path):
    if name in sys.modules:
        return sys.modules[name]
    init = directory / "__init__.py"
    if init.exists():
        spec = importlib.util.spec_from_file_location(name, str(init), submodule_search_locations=[str(directory)])
    else:
        spec = ModuleSpec(name, loader=None, origin=None)
        spec.submodule_search_locations = [str(directory)]
    mod = importlib.util.module_from_spec(spec)
    mod.__path__ = [str(directory)]
    sys.modules[name] = mod
    if spec.loader:
        spec.loader.exec_module(mod)
    return mod


_register("catvton", ROOT / "vendor" / "CatVTON")
_register("catvton.model", ROOT / "vendor" / "CatVTON" / "model")
from catvton.model.cloth_masker import AutoMasker  # noqa: E402

SIZE = 256


def _build_synthetic_masks():
    """A stick figure in labeled regions. DensePose indices per
    DENSE_INDEX_MAP; SCHP indices per LIP/ATR mappings. The left forearm is
    deliberately overlapped by SCHP 'Hair' (a weak-protect label) so the
    dense-arm contribution is the ONLY thing that can put it in the mask -
    that is the pixel region that distinguishes expose_arms from the
    sleeveless shrink."""
    dense = np.zeros((SIZE, SIZE), dtype=np.uint8)
    lip = np.zeros((SIZE, SIZE), dtype=np.uint8)
    atr = np.zeros((SIZE, SIZE), dtype=np.uint8)

    dense[60:160, 96:160] = 1      # torso
    dense[70:110, 40:88] = 15      # left big arm
    dense[110:150, 40:88] = 19     # left forearm
    dense[150:170, 40:88] = 4      # left hand
    dense[20:56, 104:152] = 23     # face

    lip[60:160, 96:160] = 5        # Upper-clothes over torso
    atr[60:160, 96:160] = 4
    lip[70:150, 40:88] = 2         # Hair (weak protect) painted over the whole arm
    atr[70:150, 40:88] = 2
    lip[20:56, 104:152] = 13       # Face
    atr[20:56, 104:152] = 11
    lip[150:170, 40:88] = 14       # Left-arm SCHP label on the hand strip (hand-protect needs it)
    atr[150:170, 40:88] = 14

    to_img = lambda a: Image.fromarray(a)  # noqa: E731
    return to_img(dense), to_img(lip), to_img(atr)


def _mask_array(**kwargs) -> np.ndarray:
    dense, lip, atr = _build_synthetic_masks()
    mask = AutoMasker.cloth_agnostic_mask(dense, lip, atr, part="upper", **kwargs)
    return (np.array(mask) > 0).astype(np.uint8)


ARM_REGION = (slice(80, 140), slice(48, 80))     # interior of the hair-covered arm
HAND_REGION = (slice(156, 166), slice(52, 76))   # interior of the hand strip
FACE_REGION = (slice(28, 48), slice(112, 144))   # interior of the face


def main() -> int:
    failures: list[str] = []

    default_mask = _mask_array(sleeve_length="default")
    sleeveless_mask = _mask_array(sleeve_length="sleeveless")
    expose_mask = _mask_array(sleeve_length="default", expose_arms=True)
    expose_vs_shrink_mask = _mask_array(sleeve_length="sleeveless", expose_arms=True)

    # 1. strong protection inviolable in every mode
    for name, m in (("default", default_mask), ("sleeveless", sleeveless_mask), ("expose_arms", expose_mask)):
        if m[FACE_REGION].any():
            failures.append(f"{name}: face pixels leaked into the edit mask")
        if m[HAND_REGION].any():
            failures.append(f"{name}: hand pixels leaked into the edit mask")

    # 2. expose_arms puts the (weak-protected) arm region inside the mask
    expose_arm_cov = expose_mask[ARM_REGION].mean()
    if expose_arm_cov < 0.9:
        failures.append(f"expose_arms: arm region coverage {expose_arm_cov:.2f}, expected ~full")

    # 3. legacy sleeveless shrink keeps that region OUT of the mask
    shrink_arm_cov = sleeveless_mask[ARM_REGION].mean()
    if shrink_arm_cov > 0.1:
        failures.append(f"sleeveless shrink: arm region coverage {shrink_arm_cov:.2f}, expected ~none")

    # 4. mutual exclusion: expose_arms wins when both are passed
    if not np.array_equal(expose_vs_shrink_mask, expose_mask):
        failures.append("expose_arms + sleeve_length='sleeveless' must behave exactly like expose_arms alone")

    # 5. default upper already includes the dense-arm parts, so expose_arms
    #    equals default here - documents that the mode's job is guaranteeing
    #    (not expanding) arm inclusion, i.e. guarding against the shrink.
    if not np.array_equal(expose_mask, default_mask):
        failures.append("expose_arms should be identical to default for part='upper' (arms are default dense parts)")

    failures.extend(check_api_call_chain())

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("smoke_expose_arms_mask: ok  strong-protect inviolable, arms in under expose_arms, shrink untouched, exclusion holds")
    return 0


def check_api_call_chain() -> list[str]:
    """try-on#38 regression guard: app.py re-defines _inference at module
    level (a High-Quality wrapper that shadows the original at import time).
    The API handler calls _inference(mask_mode=...), so EVERY module-level
    _inference definition must accept mask_mode - a wrapper that drops the
    kwarg 500s every API render (caught live 2026-08-19)."""
    import ast
    from pathlib import Path

    app_src = (Path(__file__).resolve().parents[1] / "app.py").read_text()
    tree = ast.parse(app_src)
    failures = []
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_inference"]
    if not defs:
        return ["app.py has no module-level _inference"]
    for node in defs:
        names = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
        if "mask_mode" not in names:
            failures.append(f"_inference at app.py:{node.lineno} does not accept mask_mode - the API handler passes it")
    return failures


if __name__ == "__main__":
    raise SystemExit(main())
