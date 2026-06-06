# Release Notes

## Local AI Services - Zero External Cost v1

Added a local-first image service family on top of the try-on stack.

Highlights:

- local service registry
- model pack readiness contract
- garment isolation pipeline
- product photo cleanup pipeline
- brand safety analyzer
- try-on quality gate
- local inpainting cleanup
- campaign variant generator
- event social still builder
- synthetic fixture generator
- local service reporting
- FastAPI endpoints
- CLI for operators and automation
- architecture, LLD, user guide, and tests

The first implementation uses deterministic local image operations and introduces no paid external inference/API cost.

Validation:

```bash
./.venv311/bin/python -m unittest tests.test_local_ai_services
```

GitHub handover:

- Issues `#25-#36` are implemented, commented, and closed.
- Native GitHub Projects v2 card/status updates are pending GraphQL quota reset.
- See `docs/LOCAL_AI_SERVICES.md` for exact board follow-up steps.
