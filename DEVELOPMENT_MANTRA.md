# 💎 The Virtual Try-On Development Mantra

**"Maintain Silence. Prioritize Quality. Guard the Baseline."**

This document defines the inviolable standards of the Local Try-On project. Any developer (AI or Human) must adhere to these rules to preserve the "Golden Standard" established on April 21st, 2026.

## 1. The Silence Mandate 🛡️
The terminal is the heartbeat of the Studio Experience. 
- **Rule**: A clean launch to `✓ Ready` must have zero warnings, notes, or library noise.
- **Protocol**: 
  - Silence library noise at the source (e.g., `logging` filters or `warnings.catch_warnings`).
  - Use `scheduler_config.json` updates to satisfy library expectations rather than relying on code-level silence.
  - **NEVER** use `grep` filters in `run.sh` that break the `tqdm` progress bars. Silence must be achieved internally in `app.py`.

## 2. Neural Handshake Integrity 🧬
The "neural handshake" is how we talk to the underlying AI models.
- **Rule**: Always use the most modern and performant API hooks.
- **Protocol**: 
  - Use `load_lora_adapter` (PEFT) instead of the deprecated `load_attn_procs`.
  - Ensure the Seed Generator (`torch.Generator`) is synced to the active device (`mps`) to prevent CPU-GPU transfer warnings.
  - Maintain the "Offline Ready" status—never assume a component will download its weights at runtime.

## 3. The Performance Guard 🚀
Apple Silicon performance is fragile and must be protected.
- **Rule**: Inference must always be consistent. No "Industrial Slowdowns."
- **Protocol**: 
  - Call `torch.mps.empty_cache()` and `torch.mps.synchronize()` before every generation.
  - Monitor inference times. If a 10-step draft takes more than 15s on an M-series chip, audit the GPU memory.
  - Keep the "Fast (Draft)" mode powered by `LCMScheduler` for rapid iteration.

## 4. Architectural Purity 🏛️
Complexity is the enemy of stability.
- **Rule**: Maintain the single-file `app.py` focus for as long as possible.
- **Protocol**: 
  - Avoid architectural bloat (multiple workers, separate API layers) unless definitively required for a feature.
  - Prioritize "Total Transparency"—it should be clear what every line of code is doing without digging through 10 files.

---
*If any change breaks these rules, it must be rolled back. There is no compromise on the Golden Standard.* 🛡️🎴
