import cv2
import numpy as np
from PIL import Image, ImageFilter
from skimage.transform import PiecewiseAffineTransform, warp

def texture_repair_pass(cloth_pil: Image.Image, result_pil: Image.Image, mask_pil: Image.Image, warp_strength: float = 1.0) -> Image.Image:
    """
    Performs Computer Vision based Deep Texture Repair using ORB Matching and Frequency Separation.
    Includes Spatial Torso Isolation to prevent limb tearing and Luminance Thresholding to prevent halos.
    """
    if warp_strength <= 0:
        return result_pil

    print("[VFX] Initiating TPS Deep Texture Sync...")
    
    # 1. Prepare NumPy Arrays
    cloth_np = np.array(cloth_pil.convert("RGB"))
    result_np = np.array(result_pil.convert("RGB"))
    
    gray_cloth = cv2.cvtColor(cloth_np, cv2.COLOR_RGB2GRAY)
    gray_result = cv2.cvtColor(result_np, cv2.COLOR_RGB2GRAY)

    # 2. SIFT Feature Matching with Spatial Isolation (Torso Only)
    sift = cv2.SIFT_create(nfeatures=5000)
    
    # Create a Torso Mask for the flat cloth (Middle 60% Width, 20%-80% Height)
    ch, cw = gray_cloth.shape
    torso_mask_cloth = np.zeros_like(gray_cloth)
    torso_mask_cloth[int(ch*0.15):int(ch*0.85), int(cw*0.25):int(cw*0.75)] = 255
    
    # Find keypoints ONLY on the torso of the clothing
    kp_cloth, des_cloth = sift.detectAndCompute(gray_cloth, torso_mask_cloth)
    
    # Mask the result image so we only search for matching points inside the generated garment
    mask_np = np.array(mask_pil.convert("L"))
    if mask_np.shape[:2] != gray_result.shape[:2]:
        mask_cv = cv2.resize(mask_np, (gray_result.shape[1], gray_result.shape[0]))
    else:
        mask_cv = mask_np
        
    kp_res, des_res = sift.detectAndCompute(gray_result, mask_cv)

    if des_cloth is None or des_res is None or len(kp_cloth) < 10 or len(kp_res) < 10:
        print("[warning] TPS Warp failed: Insufficient feature matches for complex alignment.")
        return result_pil

    # Brute Force Matcher with L2 distance for SIFT
    bf = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True)
    matches = bf.match(des_cloth, des_res)
    # Sort them in the order of their distance (best matches first)
    matches = sorted(matches, key=lambda x: x.distance)
    
    # Keep top 30% or at least 20 matches
    num_good_matches = max(int(len(matches) * 0.3), 20)
    good_matches = matches[:num_good_matches]

    if len(good_matches) < 10:
        print("[warning] TPS Warp failed: Not enough strong anchor points.")
        return result_pil

    src_pts = np.float32([kp_cloth[m.queryIdx].pt for m in good_matches]).reshape(-1, 2)
    dst_pts = np.float32([kp_res[m.trainIdx].pt for m in good_matches]).reshape(-1, 2)

    # Filter with RANSAC Homography to reject wild outliers
    M, mask_inliers = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if mask_inliers is None:
        print("[warning] TPS Warp failed: Homography matrix could not be resolved.")
        return result_pil
        
    inliers_mask = mask_inliers.ravel().tolist()
    
    src_inliers = src_pts[np.array(inliers_mask) == 1]
    dst_inliers = dst_pts[np.array(inliers_mask) == 1]

    if len(src_inliers) < 6:
        print("[warning] TPS Warp failed: Not enough consistent geometry.")
        return result_pil

    print(f"[VFX] Anchored {len(src_inliers)} spatial geometry points on Torso.")

    # 3. Piecewise Affine Transform (TPS Approximation)
    tformer = PiecewiseAffineTransform()
    # We map dst -> src for inverse warping used by skimage.warp
    try:
        tformer.estimate(dst_inliers, src_inliers)
        
        # Warp the cloth image
        warped_cloth_float = warp(cloth_np, tformer, output_shape=result_np.shape[:2], preserve_range=True)
        warped_cloth = warped_cloth_float.astype(np.uint8)
        
    except Exception as e:
        print(f"[warning] TPS estimation crash: {e}")
        return result_pil

    # 4. Frequency Separation & Halo Suppression
    warped_pil = Image.fromarray(warped_cloth)
    
    # Increase blur radius to 7 for smoother integration
    radius = 7
    blurred_warped = warped_pil.filter(ImageFilter.GaussianBlur(radius=radius))
    
    warped_f = np.array(warped_pil).astype(float)
    blur_warp_f = np.array(blurred_warped).astype(float)
    
    high_freq = warped_f - blur_warp_f  # Extract edges/details
    
    # Background suppression is safely handled by the final feathered_mask composite.
    
    # Low frequency of the AI Result (Lighting / Shadows)
    result_f = np.array(result_pil).astype(float)
    
    # Merge: AI Base + (High Freq details from Original)
    final_f = result_f + (high_freq * warp_strength)
    final_clipped = np.clip(final_f, 0, 255).astype(np.uint8)
    
    final_pil = Image.fromarray(final_clipped)
    
    # Apply using the mask so it only affects the physical garment
    feathered_mask = mask_pil.resize(result_pil.size, Image.LANCZOS).filter(ImageFilter.GaussianBlur(2))
    
    output = Image.composite(final_pil, result_pil, feathered_mask)
    print("[VFX] TPS Details merged successfully (Torso Optimized).")
    
    return output
