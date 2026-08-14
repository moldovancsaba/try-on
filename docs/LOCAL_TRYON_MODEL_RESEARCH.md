# Local Try-On Model Research

_Investigated 2026-08-14. Measured on the production machine, not estimated._

## TL;DR

**Do not change the model. The model was never the bottleneck.**

The current pipeline renders at ~62 s/step when the hardware is capable of ~2-2.5 s/step.
That ~25x gap is memory paging, not compute. Every candidate replacement is larger than
what already fails to stay resident, so a model swap would make throughput worse while
adding licence risk. The wins available are free: keep memory clear and cut step count.

## The machine

| | Measured |
|---|---|
| Chip | Apple M4 (base), 10 CPU / 10 GPU cores |
| Memory | 16 GB unified. `torch.mps.recommended_max_memory()` -> **12.7 GB** ceiling |
| Compute | **3.2 TFLOP/s** fp16 (4096^2 matmul) |
| Bandwidth | **84 GB/s** effective (device-to-device copy) |
| Runtime | torch 2.12 / diffusers 0.38 / transformers 5.9, MPS only, HF offline |
| Disk | 157 GB free; vault 16 GB |

Practical budget is smaller than 12.7 GB. During testing an Ollama `llama-server` held
**3.6 GB** of the 16 GB, leaving roughly 7 GB for try-on. Anything sharing this machine
comes directly out of the render budget.

## Measured performance of the current pipeline

CatVTON + SD1.5-inpainting, 768x1024, 50 steps, `images/person_example.png` +
`images/garment_example.png`, seed 42, via `POST /api/tryon/run`:

| Condition | Wall time |
|---|---|
| Contended (a second image model resident) | **92 min** |
| Machine otherwise idle | **~52 min** (~62 s/step) |

Both produced identical-size deterministic output. Neither is acceptable, and neither is
explained by the silicon.

### Diagnosis: memory-bound, not compute-bound

At 768x1024 this hardware should sustain roughly 2-2.5 s/step. It sustains 62.

Evidence it is paging, not computing:

- The app process shows near-zero RSS between renders - the weights do not stay resident.
- CPU sat at **0.9-4%** during rendering. A compute-bound render pins the GPU and keeps a
  core busy; this was waiting on disk.
- Swap reached **20.9 GB of 21.5 GB used**, with 4.4M pageouts, during the runs.

So the pipeline re-faults its weights from swap continuously. A larger model makes this
strictly worse; a faster model that still does not fit changes nothing.

## Model landscape (August 2026)

| Model | Size / licence | Fits 16 GB? | Verdict |
|---|---|---|---|
| **CatVTON + SD1.5** (current) | 899M, CC BY-NC-SA 4.0 | Yes (~2 GB) | Only viable option |
| CatVTON-FLUX | 12B, CC BY-NC 2.0 **+** FLUX.1-dev NC | No | Doubly non-commercial, 3x too large |
| IDM-VTON | SDXL, CC BY-NC-SA 4.0 | Marginal | No licence gain, slower |
| FLUX.2 klein 4B | 4B, **Apache 2.0** | **No - measured 17.94 GB peak** | Tested, overflows RAM |
| Z-Image | 6B, **Apache 2.0** | Probably not | No try-on weights exist; would need training |
| Qwen-Image-Edit | 20B, Apache 2.0 | No (32 GB+) | Out |
| Tstars-Tryon 1.0 | Taobao production | - | Weights never released |

The two Apache-2.0 models are the only ones whose licence would improve on the current
position, and neither has virtual try-on weights. Producing them means training a LoRA,
which needs a 4090-class GPU - not available here, and not something this machine can do.

## What was actually tested

**FLUX.2 klein 4B, 4-bit, via mflux** (1024^2, 8 steps, seed 42):
677 s total, ~67 s/step, **peak MLX memory 17.94 GB** - more than the machine physically
has. Dead on arrival.

Caveat worth checking before anyone retries this: mflux quantizes on load, so that peak
likely includes holding bf16 weights before quantization. A pre-quantized MLX repo (e.g.
`ar9av/FLUX.2-klein-4B-mflux-4bit`) would have a far lower peak. This does not change the
verdict - there is still no klein try-on model - but it means "17.94 GB" is a load-time
peak, not a steady-state requirement.

**mflux's CatVTON path was investigated and rejected.** `mflux-generate-in-context-catvton`
looks like an appealing "same model, faster runtime" swap. It is not: the source
(`flux_generate_in_context_catvton.py`) calls `ModelConfig.dev_fill_catvton()`, i.e.
**FLUX.1-Fill-dev, 12B**. It is three times larger than the klein model that already
overflowed, and carries the BFL non-commercial licence. Not a runtime swap - a different,
bigger, more restrictively licensed model.

## Optimal pipeline for this hardware

Keep the architecture. Change the operating conditions.

1. **Nothing else resident during renders.** Ollama alone was 22% of total RAM. This is
   the single largest and cheapest win.
2. **Cut steps from 50-84 to ~28.** SD1.5 quality plateaus well before 50; the current
   settings pay 2-3x render time for little. Re-tune on one garment before rolling out.
3. **Keep one job at a time.** Already enforced by `SingleTaskLock`, and the 92-minute
   contended render is the evidence for why that constraint is correct.
4. **Keep the online providers for quality peaks.** fal and Segmind already exist in the
   worker precisely because local capacity is limited. Nothing here changes that.
5. **Do not add a second model family to this machine.** Not klein, not Z-Image, not a
   second runtime. There is no memory for it.

Revisit only if the hardware changes. On 32 GB+ the klein and Z-Image options reopen, and
on 64 GB a FLUX-class try-on becomes reasonable.

## Licensing

CatVTON's weights are **CC BY-NC-SA 4.0** and SD1.5-inpainting has its own terms. The
service they power is free to fans, uses rights-holder-approved garments, and produces
output fans may share, print, and reuse - the strongest available case for the
non-commercial term being satisfied.

Two obligations are cheap and currently unmet:

- **BY** - attribute CatVTON and Stable Diffusion 1.5 in the UI or README.
- **SA** - derivative model weights, if ever produced, must carry the same licence.

Whether brand-promotional use by a commercial entity counts as "non-commercial" is a legal
question and is not settled here. If the rights-holder relationship is commercial, get an
opinion rather than relying on this note.

## Corrections to earlier estimates

- An earlier estimate in this investigation put a render at 2-6 minutes, extrapolated from
  third-party M4 Pro benchmarks. The measured figure is **~52 minutes**. The extrapolation
  assumed weights stay resident; on this machine they do not. Treat spec-derived
  performance estimates for this box as unreliable until memory residency is fixed.
- The idea of adopting mflux's CatVTON as a faster runtime for the same weights was wrong,
  for the reason given above.

## Open questions

- **Re-measure with clean memory.** Both benchmarks ran while swap was exhausted and
  Ollama was resident. A render under clean conditions would quantify how much of the
  62 s/step is paging. This is the next thing worth doing and it costs one render.
- Whether raising `iogpu.wired_limit_mb` helps weights stay resident on a 16 GB machine,
  and at what stability cost. Untested; approach carefully.

## Sources

Model landscape and licences were checked against the model cards and repositories
directly: [CatVTON](https://huggingface.co/zhengchong/CatVTON),
[catvton-flux-alpha](https://huggingface.co/xiaozaa/catvton-flux-alpha),
[Z-Image](https://huggingface.co/Tongyi-MAI/Z-Image),
[FLUX.2 klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B),
[mflux](https://github.com/mflux-community/mflux),
[FASHN VTON comparison](https://fashn.ai/blog/comparing-the-top-4-open-source-virtual-try-on-viton-models),
[Tstars-Tryon 1.0](https://arxiv.org/abs/2604.19748).
