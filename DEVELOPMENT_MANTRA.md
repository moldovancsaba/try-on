# 💎 The Virtual Try-On Development Mantra

**"Maintain Silence. Prioritize Quality. Guard the Baseline."**

This document defines the inviolable standards of the Local Try-On project. Every developer (AI or Human) must respect these rules to preserve the "Golden Standard" established on April 21st, 2026.

## 1. The Silence Mandate 🛡️
Terminal noise is an architectural failure.
- **Protocol**: Silence library noise (Diffusers, Transformers, Torch) at the source using internal Python loggers or context managers. 
- **The "No-Grep" Rule**: Never use `grep` filters in `run.sh` to hide errors. If a warning exists, it must be solved inside the Python core so that `tqdm` progress bars remain perfectly fluid.

## 2. The Performance Guard 🚀
Apple Silicon performance is fragile.
- **Protocol**: Call `torch.mps.empty_cache()` and `torch.mps.synchronize()` before every generation.
- **Consistency**: The shipped standalone build is `High Quality` only. If latency or memory behavior drifts, audit scheduler selection, model paths, and GPU memory fragmentation before adding new speed modes.

## 3. Structural Integrity 🏛️
Complexity must be earned, not assumed.
- **Lesson of the Industrial Era**: Rapidly switching to multi-file architectures or complex web frameworks (FastAPI) before the baseline is fully stable leads to "Industrial Bloat" and unpredictable regressions.
- **Protocol**: Maintain the single-file `app.py` focus for core logic. Only move to a multi-page structure once the feature is 100% verified in the baseline.

## 4. Documentation as Code 📖
If it isn't documented, it doesn't exist.
- **Protocol**: Any runtime contract change (device selection, offline model paths, scheduler switching, optional feature removal) must be mirrored in the `README.md` and feature-mapped in the code comments.

---
*If any change breaks these rules, it must be rolled back. There is no compromise on the Golden Standard.* 🛡️🎴
