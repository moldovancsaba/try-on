"""
Texture/logo restoration pass for try-on output.

Not reachable from the shipped pipeline: app.py forces `enable_deep_texture = False`
on every render path, so `texture_repair_pass` is currently exercised only by
tests/test_texture_repair.py. Kept for the day the override is lifted.
"""

import cv2
import numpy as np
from PIL import Image, ImageFilter
from skimage.transform import PiecewiseAffineTransform, ProjectiveTransform, warp


_COMPLEXITY_BAILOUT_THRESHOLD = 0.35
_DETAIL_EDGE_BAILOUT_THRESHOLD = 0.045
_DETAIL_LAPLACIAN_BAILOUT = 0.075


def _garment_complexity_score(cloth_pil: Image.Image) -> float:
    """
    Estimate how risky it is to apply image-feature-based warp recovery.

    Repetitive or high-frequency patterns produce ambiguous SIFT matches and
    tend to create visible scrambling or ghosting when we force a warp.
    """
    small = cloth_pil.convert("RGB").resize((64, 64), Image.LANCZOS)
    arr = np.asarray(small, dtype=float)
    std_per_channel = arr.std(axis=(0, 1))
    return min(float(std_per_channel.mean()) / 128.0, 1.0)


def _texture_detail_score(cloth_pil: Image.Image) -> tuple[float, float]:
    """
    Measure whether the garment image is detail-dense enough to make warp-based
    texture transfer likely to misalign logos or print text.
    """
    cloth_np = np.asarray(cloth_pil.convert("RGB"))
    gray = cv2.cvtColor(cloth_np, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 65, 175)
    edge_ratio = float(edges.mean()) / 255.0
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_16S, ksize=3)).astype(np.float32)
    lap_ratio = float(lap.mean()) / 255.0
    return edge_ratio, lap_ratio


def _resize_mask(mask_pil: Image.Image, target_shape: tuple[int, int]) -> np.ndarray:
    mask_np = np.asarray(mask_pil.convert("L"))
    if mask_np.shape[:2] != target_shape:
        mask_np = cv2.resize(mask_np, (target_shape[1], target_shape[0]), interpolation=cv2.INTER_NEAREST)
    return mask_np


def _build_torso_anchor_mask(height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[int(height * 0.15):int(height * 0.85), int(width * 0.25):int(width * 0.75)] = 255
    return mask


def texture_repair_decision(cloth_pil: Image.Image, warp_strength: float = 1.0) -> dict[str, float | str]:
    if warp_strength <= 0:
        return {"action": "skip", "reason": "disabled", "complexity": 0.0, "edge_ratio": 0.0, "lap_ratio": 0.0}
    complexity = _garment_complexity_score(cloth_pil)
    edge_ratio, lap_ratio = _texture_detail_score(cloth_pil)
    if complexity >= _COMPLEXITY_BAILOUT_THRESHOLD:
        return {"action": "skip", "reason": "high_complexity", "complexity": complexity, "edge_ratio": edge_ratio, "lap_ratio": lap_ratio}
    if edge_ratio >= _DETAIL_EDGE_BAILOUT_THRESHOLD or lap_ratio >= _DETAIL_LAPLACIAN_BAILOUT:
        return {"action": "skip", "reason": "fine_detail", "complexity": complexity, "edge_ratio": edge_ratio, "lap_ratio": lap_ratio}
    return {"action": "run", "reason": "eligible", "complexity": complexity, "edge_ratio": edge_ratio, "lap_ratio": lap_ratio}


def texture_repair_pass(
    cloth_pil: Image.Image,
    result_pil: Image.Image,
    mask_pil: Image.Image,
    warp_strength: float = 1.0,
) -> Image.Image:
    """
    Conservative texture repair pass for garments with stable, low-complexity detail.

    Decision:
    - Low-complexity garments: try a SIFT-driven geometric warp and blend only
      the recovered high-frequency detail.
    - High-complexity garments: skip the pass entirely and keep the diffusion
      output untouched. This is safer than scrambling text, logos, or stripes.
    """
    decision = texture_repair_decision(cloth_pil, warp_strength)
    if decision["reason"] == "disabled":
        return result_pil

    print("[VFX] Initiating TPS Deep Texture Sync...")
    complexity = float(decision["complexity"])
    edge_ratio = float(decision["edge_ratio"])
    lap_ratio = float(decision["lap_ratio"])
    print(f"[VFX] Garment complexity score: {complexity:.2f}")
    print(f"[VFX] Edge ratio: {edge_ratio:.3f}, Laplacian ratio: {lap_ratio:.3f}")

    if decision["reason"] == "high_complexity":
        print(
            "[VFX] High-variance garment texture detected; skipping texture warp "
            "to preserve branding and typography."
        )
        return result_pil

    if decision["reason"] == "fine_detail":
        print(
            "[VFX] Fine-detail garment detected; skipping texture warp to preserve "
            "logos and small print features."
        )
        return result_pil

    cloth_np = np.asarray(cloth_pil.convert("RGB"))
    result_np = np.asarray(result_pil.convert("RGB"))
    gray_cloth = cv2.cvtColor(cloth_np, cv2.COLOR_RGB2GRAY)
    gray_result = cv2.cvtColor(result_np, cv2.COLOR_RGB2GRAY)

    mask_np = _resize_mask(mask_pil, gray_result.shape[:2])
    torso_mask_cloth = _build_torso_anchor_mask(*gray_cloth.shape)

    sift = cv2.SIFT_create(nfeatures=5000)
    kp_cloth, des_cloth = sift.detectAndCompute(gray_cloth, torso_mask_cloth)
    kp_res, des_res = sift.detectAndCompute(gray_result, mask_np)

    if des_cloth is None or des_res is None or len(kp_cloth) < 10 or len(kp_res) < 10:
        print("[warning] TPS Warp skipped: insufficient feature matches.")
        return result_pil

    matches = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True).match(des_cloth, des_res)
    matches = sorted(matches, key=lambda match: match.distance)
    good_matches = matches[: max(int(len(matches) * 0.3), 20)]

    if len(good_matches) < 10:
        print("[warning] TPS Warp skipped: not enough strong anchor points.")
        return result_pil

    src_pts = np.float32([kp_cloth[m.queryIdx].pt for m in good_matches]).reshape(-1, 2)
    dst_pts = np.float32([kp_res[m.trainIdx].pt for m in good_matches]).reshape(-1, 2)

    _, mask_inliers = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if mask_inliers is None:
        print("[warning] TPS Warp skipped: homography could not be resolved.")
        return result_pil

    inlier_mask = np.asarray(mask_inliers.ravel().tolist()) == 1
    src_inliers = src_pts[inlier_mask]
    dst_inliers = dst_pts[inlier_mask]

    if len(src_inliers) < 6:
        print(
            "[VFX] TPS Warp skipped: insufficient stable feature anchors. "
            "Keeping generated output to avoid brand drift."
        )
        return result_pil

    print(f"[VFX] Anchored {len(src_inliers)} spatial geometry points on torso.")

    transform = ProjectiveTransform() if len(src_inliers) == 4 else PiecewiseAffineTransform()

    try:
        transform.estimate(dst_inliers, src_inliers)
        warped_cloth = warp(
            cloth_np,
            transform,
            output_shape=result_np.shape[:2],
            preserve_range=True,
        ).astype(np.uint8)
    except Exception as exc:
        print(f"[warning] TPS Warp skipped: transform estimation failed: {exc}")
        return result_pil

    warped_pil = Image.fromarray(warped_cloth)
    blurred_warped = warped_pil.filter(ImageFilter.GaussianBlur(radius=4))

    high_freq = np.asarray(warped_pil, dtype=float) - np.asarray(blurred_warped, dtype=float)
    final_f = np.asarray(result_pil, dtype=float) + (high_freq * warp_strength)
    final_clipped = np.clip(final_f, 0, 255).astype(np.uint8)

    feathered_mask = mask_pil.resize(result_pil.size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(2))
    output = Image.composite(Image.fromarray(final_clipped), result_pil, feathered_mask)
    print("[VFX] TPS detail merge completed.")
    return output
