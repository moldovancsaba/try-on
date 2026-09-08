# try-on in the SEYU fleet

try-on is the fleet's virtual try-on render service: a FastAPI + Gradio app (`app.py`,
CatVTON-based local pipeline) and a queue worker (`scripts/tryon_queue_worker.py`), both
launchd services on one local Mac. The app binds `127.0.0.1:7860` (loopback only, origin
guard, shared secret on the two control routes); nothing in the fleet reaches it over HTTP.
Every cross-app interaction goes through Atlas or an outbound call from the worker.

- **camera → try-on** (shared Atlas, database `camera`): camera writes `tryon_jobs`
  (`source.submissionId`, `source.imageUrl`, the garment/leather-suit reference, an
  optional assigned setup). The worker polls that collection, claims a job with a lease
  (`processing.leaseExpiresAt`, heartbeats, stale-lease recovery), downloads the source
  image and garment, renders, and drives the job through `claimed → processing →
  uploading_result → notifying_camera → done` (or `retry_wait` / `failed`). Schema and
  state machine: `docs/TRYON_ATLAS_CONTRACT.md`. Setup metadata is mirrored into
  `tryon_setups`; camera's own `/api/tryon/setups*` routes read that collection and
  write `camera_setup_preferences`.
- **try-on → camera** (outbound callback): after publishing the result the worker POSTs
  `CAMERA_TRYON_COMPLETE_URL` (camera's `POST /api/internal/tryon/complete`) with
  `x-camera-tryon-secret` = `CAMERA_TRYON_INTERNAL_SECRET`; camera stamps the
  submission and marks the job `done`. camera's 5-minute `/api/internal/tryon/sync` cron
  is the backstop when the callback fails. There is no final-failure callback: a `failed`
  job stays `queued` on the camera side until an operator acts (see the drift register).
- **Render providers** (outbound from the worker, chosen per setup / garment type):
  local CatVTON via `POST /api/tryon/run` (localhost) and the google-edge local service;
  FASHN v1.6 on fal (`FAL_KEY`, `FAL_TRYON_MODEL`, `fal.run` / `queue.fal.run`);
  Segmind IDM-VTON (`SEGMIND_API_KEY`, `api.segmind.com`). Inputs go inline as base64;
  fal is the only provider with an automatic fallback ladder.
- **Result storage**: Vercel Blob is the required primary store
  (`BLOB_READ_WRITE_TOKEN`, `public.blob.vercel-storage.com`); ImgBB (`IMGBB_API_KEY`) is
  an optional best-effort mirror. Source images are fetched only from the hosts in
  `TRYON_ALLOWED_SOURCE_HOSTS` / `TRYON_ALLOWED_PERSON_SOURCE_HOSTS` /
  `TRYON_ALLOWED_SUIT_SOURCE_HOSTS`.
- **Secrets / env** (all in the gitignored `.env.tryon-worker`; names in
  `docs/_audit/env.json`): `MONGODB_URI` + `MONGODB_DB` (or the `MONGODB_ATLAS_URI` /
  `MONGODB_DB_NAME` spellings), `CAMERA_TRYON_COMPLETE_URL`, `CAMERA_TRYON_INTERNAL_SECRET`,
  `BLOB_READ_WRITE_TOKEN`, `FAL_KEY`, `SEGMIND_API_KEY`, `IMGBB_API_KEY` (optional),
  `TRYON_LOCAL_SECRET` (gates `/api/tryon/run` and `/api/worker/service-action`). The
  worker also honours per-provider daily limits and circuit-breaker thresholds.
- **SSO**: none. try-on has no user accounts; the operator UI is loopback-only.
- **Loopback posture**: the app is never exposed; the worker is single-instance (a second
  worker exits). Operators reach the UI at `http://127.0.0.1:7860/worker-control`.

Canonical cross-app map: messmass `docs/_audit/fleet-architecture.md`. Contract:
`docs/TRYON_ATLAS_CONTRACT.md`. API surface: `docs/_audit/api-reference.md`. Operations:
`docs/RUNBOOK.md` (launchd, retry vs rerun, queue retention and sweep). Fleet rule for any
change to the above: `docs/_audit/contract-first-rule.md`.
