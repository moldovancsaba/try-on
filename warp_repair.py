import cv2
import numpy as np
from PIL import Image, ImageFilter
from skimage.transform import PiecewiseAffineTransform, ProjectiveTransform, warp


_COMPLEXITY_BAILOUT_THRESHOLD = 0.35


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


def _resize_mask(mask_pil: Image.Image, target_shape: tuple[int, int]) -> np.ndarray:
    mask_np = np.asarray(mask_pil.convert("L"))
    if mask_np.shape[:2] != target_shape:
        mask_np = cv2.resize(mask_np, (target_shape[1], target_shape[0]))
    return mask_np


def _build_torso_anchor_mask(height: int, width: int) -> np.ndarray:
    mask = np.zeros((height, width), dtype=np.uint8)
    mask[int(height * 0.15):int(height * 0.85), int(width * 0.25):int(width * 0.75)] = 255
    return mask


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
    if warp_strength <= 0:
        return result_pil

    print("[VFX] Initiating TPS Deep Texture Sync...")
    complexity = _garment_complexity_score(cloth_pil)
    print(f"[VFX] Garment complexity score: {complexity:.2f}")

    if complexity >= _COMPLEXITY_BAILOUT_THRESHOLD:
        print(
            "[VFX] Complex garment detected; skipping texture warp to avoid "
            "pattern scrambling and mask-edge ghosting."
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
        print("[VFX] Falling back to bounding-box anchors.")
        coords_cloth = cv2.findNonZero(torso_mask_cloth)
        coords_res = cv2.findNonZero(mask_np)
        if coords_cloth is None or coords_res is None:
            return result_pil

        x1, y1, w1, h1 = cv2.boundingRect(coords_cloth)
        x2, y2, w2, h2 = cv2.boundingRect(coords_res)
        src_inliers = np.float32([[x1, y1], [x1 + w1, y1], [x1 + w1, y1 + h1], [x1, y1 + h1]])
        dst_inliers = np.float32([[x2, y2], [x2 + w2, y2], [x2 + w2, y2 + h2], [x2, y2 + h2]])
    else:
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
    blurred_warped = warped_pil.filter(ImageFilter.GaussianBlur(radius=7))

    high_freq = np.asarray(warped_pil, dtype=float) - np.asarray(blurred_warped, dtype=float)
    final_f = np.asarray(result_pil, dtype=float) + (high_freq * warp_strength)
    final_clipped = np.clip(final_f, 0, 255).astype(np.uint8)

    feathered_mask = mask_pil.resize(result_pil.size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(2))
    output = Image.composite(Image.fromarray(final_clipped), result_pil, feathered_mask)
    print("[VFX] TPS detail merge completed.")
    return output
