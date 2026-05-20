import sys
import os
import cv2
import torch
import numpy as np
from PIL import Image
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model_paths import get_models_root

# Load the vendored CatVTON package from the local workspace.
sys.path.insert(0, str(PROJECT_ROOT / 'vendor'))

from CatVTON.model.cloth_masker import AutoMasker

print("Loading DensePose AutoMasker...")
models_root = get_models_root()
masker = AutoMasker(
    densepose_ckpt=str(models_root / "processors" / "catvton-segmentation" / "DensePose"),
    schp_ckpt=str(models_root / "processors" / "catvton-segmentation" / "SCHP"),
    device="cpu"
)

# Use the centered mannequin reference image for master-map generation.
img_path = str(PROJECT_ROOT / 'images' / 'mannequins_2.png')
print(f"Loading {img_path}...")
img = Image.open(img_path).convert("RGB")
w, h = img.size
slice_w = w // 3

out_dir = str(PROJECT_ROOT / 'studio_tools' / 'master_maps')
os.makedirs(out_dir, exist_ok=True)

# Export maps in the visual order expected by the studio UI: left, front, right.
names = ["left", "front", "right"]

for i in range(3):
    print(f"Processing Mannequin {names[i]}...")
    
    # Save the mannequin crop used as the studio preview image.
    slice_img = img.crop((i * slice_w, 0, (i + 1) * slice_w, h))
    img_save_path = os.path.join(out_dir, f"mannequin_{names[i]}.png")
    slice_img.save(img_save_path)
    
    # Extract the DensePose label map for this mannequin view.
    preprocessed = masker.preprocess_image(slice_img)
    densepose_mask = preprocessed['densepose']  # PIL image with DensePose label IDs.
    
    # Save the DensePose map as a PNG for canvas-based studio tooling.
    dp_save_path = os.path.join(out_dir, f"master_map_{names[i]}.png")
    densepose_mask.save(dp_save_path)
    
print("Master Maps generated successfully!")
