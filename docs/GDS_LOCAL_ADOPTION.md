# GDS local adoption notes

## Installed baseline

- `@sovereignsquad/gds-core`: `3.9.0`
- `@sovereignsquad/gds-admin`: `3.9.0`
- `@sovereignsquad/gds-theme`: `3.9.0`

The local try-on environment is a Python/FastAPI/Gradio app with static Jinja templates. It does not currently run a React application shell, so GDS React components cannot be used directly without a frontend migration.

## What is new and useful in GDS 3.4

- Theme presets and vibe tokens through `@sovereignsquad/gds-theme`.
- Mobile navigation behavior support for collapsible app shells.
- Standard state components such as `StateBlock`, `StatusBadge`, `MetricCard`, `ProgressCard`, `ResultSummary`, and `InlineAlert`.
- Operator/admin surfaces such as `ResponsiveDataView`, `AdminReviewLayout`, `EditorScaffold`, `StatsStrip`, `WorkspaceHeader`, and standardized form sections.
- Public flow components for capture/share-style flows.
- Accessibility-focused upload, confirmation, semantic action, notification, and telemetry primitives.
- Layout/schema helpers for consistent documentation, dashboard, and reporting screens.

## Implemented local bridge

`studio_tools/static/global.css` defines a static GDS 3.9 bridge:

- maps local colors to `--gds-vibe-*` tokens
- applies GDS-like surface gradients, card shadows, and button motion
- provides GDS component mirrors: `.badge` (StatusBadge), `.state-block` (StateBlock),
  `.metric-card` (MetricCard), `.card-grid`/`.data-card` (ResponsiveDataView),
  `.notice` (InlineAlert), `.dropzone` (accessible upload states)
- CSS-only mobile nav collapse + `prefers-reduced-motion` support
- preserves existing FastAPI/Jinja templates and Gradio pages
- avoids introducing a second frontend runtime

## Implemented operator surfaces (GDS 3.9 adoption)

1. Worker Control page — **done**
   - Status badges for Running, Active Job, Backpressure, Provider Timeout, and Disabled.
   - `StateBlock` for degraded queue health (worker down, disabled, queue error, open circuit).
   - Backpressure reason and oldest-ready age surfaced in Queue Health.

2. Queue and provider observability — **done**
   - Provider Scorecard panel with metric-card semantics (success rate, p50 latency).
   - Per-provider failure/timeout/slow breakdown and circuit state.
   - Note: failures are split at provider granularity from the worker scorecard; a finer
     taxonomy (validation vs upload vs callback) would need dedicated status fields.

3. Garment Library — **done (partial)**
   - Responsive `.card-grid` with `.data-card` items and an accessible empty state.
   - `View Package` action served via the new `/packages` static mount.
   - Rebuild / Download / Disable actions still need backend endpoints before wiring.

4. Setup Garment page — **done (partial)**
   - Browser `alert()` replaced with inline accessible `.notice` messages (`aria-live`).
   - Dropzone with idle / drag-over / selected / error states + client type/size validation.
   - Keyboard undo ('U') retained; full keyboard point-placement on the canvas is deferred.

5. Landing page and navigation — **done**
   - CSS-only mobile collapse menu, `aria-current="page"` on active links, reduced-motion-safe hover.

6. Gradio try-on surfaces — **done**
   - Compact operations banner above the Gradio apps reporting model readiness and worker state
     (server-rendered snapshot per page load).

## Migration boundary

Do not import React GDS components into `app.py` or Jinja templates directly. If we want native GDS components, create a separate Next/React operator console or mount a bundled React app under a dedicated FastAPI static route.
