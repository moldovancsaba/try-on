# try-on operations runbook

Operational reference for the two local launchd services. Fleet version 12.2.0.
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
- `queue/done` + `queue/failed` grow unbounded today; a retention policy is
  tracked in try-on#45. Until it lands, prune manually with care (never delete a
  leased/in-flight job).

## Provider routing (operational)
Garment-typed jersey/top/bottom jobs on a Segmind setup render on FASHN v1.6
(fal) — this is a billing-relevant reroute. Motorsport suits and explicit
local/google setups keep their pipeline. fal is the only provider with automatic
fallback ladders (pre-dispatch, mid-render, startup probe).

## Logs
`queue/logs/app.stdout.log`, `app.stderr.log`, `worker.stdout.log`,
`worker.stderr.log`.
