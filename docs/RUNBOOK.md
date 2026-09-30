# try-on operations runbook

Operational reference for the two local launchd services. Fleet version 12.3.40.
Companion to `HANDOVER.md` (state) and `docs/TRYON_ATLAS_CONTRACT.md` (contract).

## Services (launchd, user domain)
| Label | What | Port / role | Plist |
|---|---|---|---|
| `com.tryon.app-server` | FastAPI + Gradio render server | `127.0.0.1:7860` (loopback only; origin-guarded) | `launchd/com.tryon.app-server.plist` |
| `com.tryon.camera-worker` | Atlas queue worker | claims/leases `tryon_jobs` | `launchd/com.tryon.camera-worker.plist` |

Both are `KeepAlive=true` (auto-restart on exit). Secrets come from
`.env.tryon-worker` / `.env.local`, never the plists.

## Restart
```bash
launchctl kickstart -k gui/$(id -u)/com.tryon.app-server      # render server
launchctl kickstart -k gui/$(id -u)/com.tryon.camera-worker   # worker
```
The app-server reloads models on restart (~30-60s); watch
`queue/logs/app.stdout.log` for "Ready | Backend: MPS". A restarted worker
immediately re-queues its own in-flight jobs (`recover_interrupted_owned_jobs`).

## Verify healthy
```bash
curl -s http://127.0.0.1:7860/api/capabilities | jq .assets   # app-server ready
tail -f queue/logs/worker.stdout.log                          # worker claims
```
Cross-origin requests are refused (403) by design; loopback/no-Origin callers pass.
The two control routes, `POST /api/tryon/run` and `POST /api/worker/service-action`,
additionally need the `x-tryon-local-secret` header equal to `TRYON_LOCAL_SECRET`
(set in `.env.tryon-worker`, read by both services at start). Without it they answer
401; with the variable unset they refuse every request, which stalls every local render
the worker dispatches. The worker and the Worker Control page send it themselves.

## Troubleshooting
- `launchctl print gui/$(id -u)/com.tryon.camera-worker` shows `last exit code = 1`
  right after a boot: usually the worker started before DNS was ready and the Atlas SRV
  lookup failed (`dns.resolver.NoNameservers` at the end of
  `queue/logs/worker.stderr.log`). `KeepAlive` restarts it; if the heartbeat in
  `GET /api/worker/status` is fresh, nothing to do. Seen at the 2026-09-28 02:22 boot.
- Local renders fail with `local_tryon_api_failed:401`: `TRYON_LOCAL_SECRET` is missing
  or differs between what the app-server loaded and what the worker sends. Fix the env
  file, then restart both services.

## Retry vs rerun (know the difference)
- **Retry** (`POST /api/tryon/jobs/{id}/retry`): same job/settings back to the
  queue. `resetAttempts:true` zeroes the attempt count (refills the retry budget).
- **Rerun** (camera admin → Try-On Queue): a NEW job from the same photo+garment,
  superseding the prior result; the new result re-enters moderation. Use rerun
  when quality or setup must change.

## Queue admin
- Workspaces live under `queue/{incoming,processing,done,failed}`; Atlas is the
  source of truth for job STATE.
- Stuck/orphaned workspaces: `queue/processing/<job>` left by a hard kill is
  reconciled from Atlas, not the filesystem. Run
  `python3 scripts/tryon_infra_cli.py reconcile` before deleting any orphan.
- `queue/done` + `queue/failed` retention (try-on#45): `python3
  scripts/tryon_infra_cli.py prune-queue [--days 30] [--keep 200] [--apply]`.
  Dry-run by default; prunes terminal buckets only (never queue/processing, so
  no leased/in-flight job is touched). Run periodically or wire to a cron.
- `queue/processing` sweep (try-on#45): `python3 scripts/tryon_infra_cli.py
  sweep-processing [--grace-minutes 60] [--apply]`. Looks each
  `queue/processing/<jobId>` up in Atlas and prints a per-dir verdict:
  `done` -> move to `queue/done`; `failed` -> move to `queue/failed`; an active
  status (claimed/processing/uploading_result/notifying_camera) with a live
  lease -> skipped (in flight); an active status whose lease expired more than
  `--grace-minutes` ago, or `retry_wait` -> reported as stale-lease and left in
  place (the worker's own `recover_stale_jobs` re-queues those; moving the
  workspace would strand the requeue); no Atlas record -> reported, never
  touched. Dry-run by default. Use `--apply` only after a dry-run shows nothing
  but `MOVE` verdicts you expect, and never while the worker is mid-job on one
  of the listed dirs (`GET /api/worker/status` -> `currentJobId`). Once moved,
  the dirs age out through `prune-queue`. Safety proof:
  `python3 scripts/smoke_sweep_processing.py`.

## Provider routing (operational)
Garment-typed jersey/top/bottom jobs on a Segmind setup render on FASHN v1.6
(fal) — this is a billing-relevant reroute. Motorsport suits and explicit
local/google setups keep their pipeline. fal is the only provider with automatic
fallback ladders (pre-dispatch, mid-render, startup probe).

## Logs
`queue/logs/app.stdout.log`, `app.stderr.log`, `worker.stdout.log`,
`worker.stderr.log` (paths set in the launchd plists), plus the worker's structured
events in `.runtime/worker_events.ndjson`. Nothing rotates them: no newsyslog entry, no
rotating handler. They are small today (largest ~8 MB on 2026-09-28) but grow without
bound; add rotation (e.g. a newsyslog rule) if they start to matter.

## Smoke scripts
Run with the project venv (`./.venv311/bin/python scripts/<name>.py`; several import the
worker, numpy or the vendored masker); each exits non-zero on failure. None needs Atlas
or a running app-server, and none renders.
- `smoke_expose_arms_mask.py`: expose_arms mask geometry on synthetic label maps
  (try-on#38).
- `smoke_garment_type_resolution.py`: garment-type → category/sleeve resolution across the
  local, Segmind and fal vocabularies (try-on#37).
- `smoke_outfit_orchestration.py`: two-pass outfit order, fail-fast checks and atomicity
  against stubs (try-on#39).
- `smoke_local_api_fencing.py`: source-level check of the origin guard, output-path
  containment, the 401 secret gate on the two control routes, and an X.Y.Z app version
  (try-on#42).
- `smoke_prune_queue.py`: `prune-queue` retention predicate (try-on#45).
- `smoke_sweep_processing.py`: `sweep-processing` never moves leased, expired-lease or
  record-less workspaces (try-on#45).
