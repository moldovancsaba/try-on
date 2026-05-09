import sys
import os
import cv2
import torch
import numpy as np
from PIL import Image
import json

# Add vendor to path
sys.path.insert(0, '/Users/Shared/Projects/try-on/vendor')

from CatVTON.model.cloth_masker import AutoMasker

print("Loading DensePose AutoMasker...")
masker = AutoMasker(
    densepose_ckpt="/Users/Shared/Models/processors/catvton-segmentation/DensePose",
    schp_ckpt="/Users/Shared/Models/processors/catvton-segmentation/SCHP",
    device="cpu"
)

# Use mannequins_2.png which is better centered
img_path = '/Users/Shared/Projects/try-on/images/mannequins_2.png'
print(f"Loading {img_path}...")
img = Image.open(img_path).convert("RGB")
w, h = img.size
slice_w = w // 3

out_dir = '/Users/Shared/Projects/try-on/studio_tools/master_maps/'
os.makedirs(out_dir, exist_ok=True)

# CORRECT VISUAL ORDER: [Left, Front, Right]
names = ["left", "front", "right"]

for i in range(3):
    print(f"Processing Mannequin {names[i]}...")
    
    # 1. Slice and save the raw visual mannequin for the Web UI
    slice_img = img.crop((i * slice_w, 0, (i + 1) * slice_w, h))
    img_save_path = os.path.join(out_dir, f"mannequin_{names[i]}.png")
    slice_img.save(img_save_path)
    
    # 2. Extract DensePose (The Master Map)
    preprocessed = masker.preprocess_image(slice_img)
    densepose_mask = preprocessed['densepose'] # PIL Image where pixel values are 0-24
    
    # Save the DensePose mask as a PNG so Web UI JS can read it via Canvas
    dp_save_path = os.path.join(out_dir, f"master_map_{names[i]}.png")
    densepose_mask.save(dp_save_path)
    
print("Master Maps generated successfully!")
