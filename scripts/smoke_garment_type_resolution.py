"""Smoke test for garment-type render-parameter resolution (try-on#37).

Offline: no database, no providers, no model. Verifies the single resolution
function's precedence rule (job-level garmentType wins over the setup preset;
absent/unrecognized falls back to the setup verbatim), the sleeveStyle ->
sleeve_length mapping, and - critically - that the resolved category alias
strings normalize correctly through ALL THREE provider vocabularies (app.py's
_normalize_category, the worker's _normalize_segmind_category, and the fal
coercion sets), since the long UI enum strings would silently break Segmind's
lower-body case.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))
from tryon_queue_worker import (  # noqa: E402
    GARMENT_TYPE_TO_CATEGORY,
    SLEEVE_STYLE_TO_SLEEVE_LENGTH,
    _normalize_segmind_category,
    resolve_render_params,
)

# fal's mapping sets, mirrored from TryOnWorker._coerce_fal_category (an
# instance method on a worker that needs Mongo to construct - the sets are
# what the smoke asserts against, and a drift here means the real method
# changed and this test must be updated with it).
_FAL_ONE_PIECES_KEYS = {"one-piece", "onepieces", "onepiece", "one_pieces", "one pieces", "dresses", "dresses_only", "dress"}
_FAL_BOTTOMS_KEYS = {"lower", "lower_body", "lower body", "bottoms", "bottom"}
_FAL_TOPS_KEYS = {"upper", "upper_body", "upper body", "tops", "top"}

# app.py's alias table, mirrored for the same reason (importing app.py loads
# torch/gradio - far too heavy for a smoke test).
_APP_ALIASES = {"upper": "Upper (T-Shirts, Hoodies)", "lower": "Lower (Jeans, Shorts, Skirts)", "dresses": "Full-Body (Suits, Dresses, Rompers)"}


def _fal_category(value: str) -> str:
    key = value.strip().lower()
    if key in _FAL_ONE_PIECES_KEYS:
        return "one-pieces"
    if key in _FAL_BOTTOMS_KEYS:
        return "bottoms"
    if key in _FAL_TOPS_KEYS:
        return "tops"
    return "auto"


def main() -> int:
    failures: list[str] = []
    setup = {"category": "dresses", "sleeve_length": "default"}

    # --- every garmentType cell resolves and survives all three providers ---
    expectations = {
        "motorsport_suit": ("dresses", "Full-Body (Suits, Dresses, Rompers)", "dresses", "one-pieces"),
        "jersey": ("upper", "Upper (T-Shirts, Hoodies)", "upper_body", "tops"),
        "top": ("upper", "Upper (T-Shirts, Hoodies)", "upper_body", "tops"),
        "bottom": ("lower", "Lower (Jeans, Shorts, Skirts)", "lower_body", "bottoms"),
    }
    for gtype, (alias, app_category, segmind, fal) in expectations.items():
        resolved = resolve_render_params({"garmentType": gtype}, setup)
        if resolved["source"] != "garment_type":
            failures.append(f"{gtype}: expected source=garment_type, got {resolved['source']}")
        if resolved["category"] != alias:
            failures.append(f"{gtype}: expected category {alias!r}, got {resolved['category']!r}")
        if _APP_ALIASES.get(resolved["category"]) != app_category:
            failures.append(f"{gtype}: alias {resolved['category']!r} does not normalize to app category {app_category!r}")
        if _normalize_segmind_category(resolved["category"]) != segmind:
            failures.append(f"{gtype}: segmind normalization mismatch, got {_normalize_segmind_category(resolved['category'])!r}")
        if _fal_category(resolved["category"]) != fal:
            failures.append(f"{gtype}: fal normalization mismatch, got {_fal_category(resolved['category'])!r}")

    # --- sleeveStyle mapping, including long_sleeve -> default. Sleeveless on
    # a jersey/top is the expose_arms case (try-on#38): sleeve_length is
    # forced to 'default' (the shrink and the exposure are mutually
    # exclusive) and mask_mode carries the intent instead. ---
    for style, expected in SLEEVE_STYLE_TO_SLEEVE_LENGTH.items():
        resolved = resolve_render_params({"garmentType": "jersey", "sleeveStyle": style}, setup)
        expected_mask = "expose_arms" if style == "sleeveless" else "default"
        expected_sleeve = "default" if style == "sleeveless" else expected
        if resolved["sleeve_length"] != expected_sleeve:
            failures.append(f"sleeveStyle {style}: expected sleeve {expected_sleeve!r}, got {resolved['sleeve_length']!r}")
        if resolved["mask_mode"] != expected_mask:
            failures.append(f"sleeveStyle {style}: expected mask_mode {expected_mask!r}, got {resolved['mask_mode']!r}")

    # expose_arms triggers for top too, never for bottom/motorsport_suit
    if resolve_render_params({"garmentType": "top", "sleeveStyle": "sleeveless"}, setup)["mask_mode"] != "expose_arms":
        failures.append("top + sleeveless should trigger expose_arms")
    for gtype in ("bottom", "motorsport_suit"):
        resolved = resolve_render_params({"garmentType": gtype, "sleeveStyle": "sleeveless"}, setup)
        if resolved["mask_mode"] != "default":
            failures.append(f"{gtype} + sleeveless must never trigger expose_arms, got {resolved['mask_mode']!r}")

    # sleeveStyle absent with garmentType present -> setup's sleeve_length
    resolved = resolve_render_params({"garmentType": "jersey"}, {"category": "dresses", "sleeve_length": "short_sleeve"})
    if resolved["sleeve_length"] != "short_sleeve":
        failures.append(f"absent sleeveStyle should inherit setup sleeve_length, got {resolved['sleeve_length']!r}")

    # --- legacy job (no fields): setup verbatim, source=setup, no mask mode ---
    resolved = resolve_render_params({}, {"category": "dresses", "sleeve_length": "sleeveless"})
    if resolved != {"category": "dresses", "sleeve_length": "sleeveless", "mask_mode": "default", "source": "setup"}:
        failures.append(f"legacy job should resolve to setup verbatim, got {resolved}")

    # sleeveStyle without garmentType is treated as legacy (precedence keys on garmentType alone)
    resolved = resolve_render_params({"sleeveStyle": "sleeveless"}, setup)
    if resolved["source"] != "setup":
        failures.append("sleeveStyle without garmentType must not trigger garment-type resolution")

    # --- unrecognized future type: logged fallback, never a crash ---
    resolved = resolve_render_params({"garmentType": "cape_of_the_future"}, setup)
    if resolved["source"] != "setup" or resolved["category"] != "dresses":
        failures.append(f"unrecognized type should fall back to setup, got {resolved}")

    # --- setup with no category at all (legacy fallback setup): Nones, caller keeps payload untouched ---
    resolved = resolve_render_params({}, {})
    if resolved["category"] is not None or resolved["sleeve_length"] is not None:
        failures.append(f"empty setup should resolve to None values (payload left untouched), got {resolved}")

    # mapping tables complete and closed
    if set(GARMENT_TYPE_TO_CATEGORY) != {"motorsport_suit", "jersey", "top", "bottom"}:
        failures.append(f"GARMENT_TYPE_TO_CATEGORY keys drifted: {sorted(GARMENT_TYPE_TO_CATEGORY)}")


    # --- segmind transparent-PNG rules (live regression 2026-08-19): the
    # alpha-halo hack must NOT stomp a garment-typed category back to
    # full-body "dresses"; setup-derived categories keep the old forcing ---
    from tryon_queue_worker import _apply_segmind_transparent_png_rules

    req = {"category": "upper_body", "garment_des": "Sport jersey"}
    _apply_segmind_transparent_png_rules(req, {"category_source": "garment_type"})
    if req["category"] != "upper_body":
        failures.append(f"alpha rules stomped garment-typed category: {req['category']}")
    if "alpha edge" not in req["garment_des"]:
        failures.append("alpha rules must still add the alpha-edge prompt for garment-typed renders")

    req = {"category": "upper_body", "garment_des": "Sport jersey"}
    _apply_segmind_transparent_png_rules(req, {"category_source": "setup"})
    if req["category"] != "dresses":
        failures.append(f"alpha rules should still force dresses for setup-derived category, got {req['category']}")

    req = {"category": "upper_body", "garment_des": "Sport jersey"}
    _apply_segmind_transparent_png_rules(req, {})
    if req["category"] != "dresses":
        failures.append(f"alpha rules should force dresses when category_source is absent, got {req['category']}")


    # --- segmind sleeve steering (live regression 2026-08-19): sleeveless and
    # short-sleeve jerseys must carry their sleeve constraint in garment_des;
    # motorsport suits must not ---
    import types
    from tryon_queue_worker import TryOnQueueWorker

    coerce = types.MethodType(TryOnQueueWorker._coerce_segmind_payload, object())
    des = coerce({"mask_mode": "expose_arms", "category_source": "garment_type"})["garment_des"].lower()
    if "sleeveless jersey" not in des or "completely bare" not in des:
        failures.append("segmind garment_des missing sleeveless constraint under expose_arms")
    des = coerce({"sleeve_length": "short_sleeve", "mask_mode": "default"})["garment_des"].lower()
    if "short sleeves" not in des:
        failures.append("segmind garment_des missing short-sleeve constraint")
    des = coerce({"mask_mode": "default", "sleeve_length": "default"})["garment_des"].lower()
    if "sleeveless jersey" in des or "short sleeves ending" in des:
        failures.append("segmind garment_des must not carry sleeve constraints for default renders")


    # --- fal rerouting (live decision 2026-08-19): garment-typed jerseys/
    # tops/bottoms leave Segmind for FASHN; motorsport suits and explicit
    # local/google setups stay put ---
    from tryon_queue_worker import should_reroute_garment_typed_render_to_fal, _image_data_uri

    if not should_reroute_garment_typed_render_to_fal("garment_type", "jersey", "segmind_idm_vton"):
        failures.append("jersey on segmind must reroute to fal")
    if should_reroute_garment_typed_render_to_fal("garment_type", "motorsport_suit", "segmind_idm_vton"):
        failures.append("motorsport suit must NOT reroute to fal")
    if should_reroute_garment_typed_render_to_fal("setup", "jersey", "segmind_idm_vton"):
        failures.append("setup-derived render must NOT reroute to fal")
    if should_reroute_garment_typed_render_to_fal("garment_type", "jersey", "motogp_leather_magic"):
        failures.append("local-profile render must NOT reroute to fal")

    # --- data URIs replace ImgBB for fal inputs (ImgBB outage 2026-08-19) ---
    import base64 as _b64, io as _io, tempfile
    from PIL import Image as _Img
    # Transparent garments MUST be composited onto white, never sent as raw
    # alpha: FASHN flattens alpha to black, which made the Debrecen jersey's
    # black side panels read as long sleeves (live 2026-08-19).
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        _Img.new("RGBA", (8, 8), (255, 0, 0, 0)).save(tmp.name)
        uri = _image_data_uri(Path(tmp.name))
    if not uri.startswith("data:image/jpeg;base64,"):
        failures.append(f"alpha image data URI must be white-composited JPEG, got: {uri[:40]}")
    else:
        decoded = _Img.open(_io.BytesIO(_b64.b64decode(uri.split(",", 1)[1]))).convert("RGB")
        r, g, b = decoded.getpixel((4, 4))
        if min(r, g, b) < 240:
            failures.append(f"fully transparent pixel should composite to white, got {(r, g, b)}")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("smoke_garment_type_resolution: ok  all mapping cells, all three providers, legacy + unrecognized fallbacks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
