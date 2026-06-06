# Local AI Services User Guide

## Daily Operator Flow

1. Check service availability.

```bash
./.venv311/bin/python scripts/local_ai_services.py list
```

2. Check model pack readiness.

```bash
./.venv311/bin/python scripts/local_ai_services.py model-packs
```

3. Run a service with a JSON payload.

```bash
./.venv311/bin/python scripts/local_ai_services.py run garment_isolation --payload payload.json
```

4. Review generated artifacts under:

```text
.runtime/local_ai/artifacts/
```

5. Export operational reporting.

```bash
./.venv311/bin/python scripts/local_ai_services.py report
```

## Example Payloads

Garment isolation:

```json
{
  "jobId": "sample_garment",
  "sourceImagePath": "/absolute/path/garment-source.png"
}
```

Product cleanup:

```json
{
  "jobId": "sample_cleanup",
  "inputImagePath": "/absolute/path/input.png",
  "outputRatios": ["1:1", "4:5", "9:16"],
  "backgroundMode": "white"
}
```

Brand safety:

```json
{
  "sourceImagePath": "/absolute/path/garment.png",
  "outputImagePath": "/absolute/path/generated.png"
}
```

Quality gate:

```json
{
  "sourceImagePath": "/absolute/path/garment.png",
  "outputImagePath": "/absolute/path/generated.png"
}
```

Repair:

```json
{
  "inputImagePath": "/absolute/path/generated.png",
  "maskImagePath": "/absolute/path/mask.png",
  "repairMode": "garment_edge"
}
```

## Interpreting Results

`pass` means the local check did not detect a blocking problem.

`warn` means a human should review the output before using or publishing it.

`fail` means the output should be rerun, repaired, or manually rejected.

The v1 analyzer is intentionally conservative and deterministic. It is useful for triage, not final legal or brand approval.

## GDS Console Requirement

Any browser-based console for these services must use only the Sovereign Squad General Design System. Required states are loading, empty, unavailable, disabled, queued, running, retrying, completed, failed, partially failed, cancelled, and recovered.

