# GDS local adoption notes

## Installed baseline

- `@doneisbetter/gds-core`: `3.4.3`
- `@doneisbetter/gds-admin`: `3.4.3`
- `@doneisbetter/gds-theme`: `3.4.3`

The local try-on environment is a Python/FastAPI/Gradio app with static Jinja templates. It does not currently run a React application shell, so GDS React components cannot be used directly without a frontend migration.

## What is new and useful in GDS 3.4

- Theme presets and vibe tokens through `@doneisbetter/gds-theme`.
- Mobile navigation behavior support for collapsible app shells.
- Standard state components such as `StateBlock`, `StatusBadge`, `MetricCard`, `ProgressCard`, `ResultSummary`, and `InlineAlert`.
- Operator/admin surfaces such as `ResponsiveDataView`, `AdminReviewLayout`, `EditorScaffold`, `StatsStrip`, `WorkspaceHeader`, and standardized form sections.
- Public flow components for capture/share-style flows.
- Accessibility-focused upload, confirmation, semantic action, notification, and telemetry primitives.
- Layout/schema helpers for consistent documentation, dashboard, and reporting screens.

## Implemented local bridge

`studio_tools/static/global.css` now defines a static GDS 3.4 bridge:

- maps local colors to `--gds-vibe-*` tokens
- applies GDS-like surface gradients, card shadows, and button motion
- preserves existing FastAPI/Jinja templates and Gradio pages
- avoids introducing a second frontend runtime

## Recommended next implementation for the local try-on app

1. Worker Control page
   - Add GDS-style status badges for Running, Active Job, Backpressure, Provider Timeout, and Disabled.
   - Add a visible `StateBlock` equivalent for degraded queue health.
   - Show oldest queued age and backpressure reason as first-class fields.

2. Queue and provider observability
   - Add a Provider Scorecard panel using GDS metric-card semantics.
   - Split timeout, provider, local runtime, validation, upload, and Camera callback failures.
   - Add operator guidance beside each failure class.

3. Garment Library
   - Replace the plain list with a responsive card grid.
   - Add empty, loading, and error states.
   - Add accessible actions for View, Rebuild Asset, Download Package, and Disable.

4. Setup Garment page
   - Replace browser `alert()` flows with inline accessible notices.
   - Add upload/dropzone states: idle, selected, failed, unsupported type, too large.
   - Add keyboard-visible point placement guidance and undo feedback.

5. Landing page and navigation
   - Add a mobile collapse menu following the GDS mobile navigation behavior.
   - Add aria-current on active navigation links.
   - Add reduced-motion-safe card hover behavior.

6. Gradio try-on surfaces
   - Keep Gradio theme values mapped to local tokens.
   - Add a compact operations banner above Gradio apps that reports model readiness and worker state.

## Migration boundary

Do not import React GDS components into `app.py` or Jinja templates directly. If we want native GDS components, create a separate Next/React operator console or mount a bundled React app under a dedicated FastAPI static route.
