"""Smoke: try-on#42 API fencing (source-level).

Reads app.py only - no server, no network, no render. Asserts:
- the origin-guard middleware and the render-path containment exist;
- /api/tryon/run and /api/worker/service-action carry the
  `_require_local_secret` dependency, and that dependency answers 401 for a
  missing/wrong `x-tryon-local-secret` and fails closed when the secret is unset;
- the FastAPI app version is a plain X.Y.Z. It is read from app.py rather than
  hard-coded here, so a fleet lockstep bump does not break this smoke.
Live 403/401/400 behavior is checked separately against the running server.
"""
from __future__ import annotations
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECRET_GATED_ROUTES = ("/api/tryon/run", "/api/worker/service-action")


def _fastapi_version(tree: ast.Module) -> str | None:
    """Return the string `version=` keyword of the FastAPI(...) call, if any."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "FastAPI":
            for kw in node.keywords:
                if kw.arg == "version" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    return kw.value.value
    return None


def _secret_gated_paths(tree: ast.Module) -> set[str]:
    """Route paths whose decorator passes `dependencies=[...(_require_local_secret)]`."""
    gated: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and dec.args and isinstance(dec.args[0], ast.Constant)):
                continue
            for kw in dec.keywords:
                if kw.arg == "dependencies" and "_require_local_secret" in ast.unparse(kw.value):
                    gated.add(str(dec.args[0].value))
    return gated


def _function_source(tree: ast.Module, name: str) -> str:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.unparse(node)
    return ""


def main() -> int:
    src = (ROOT / "app.py").read_text()
    tree = ast.parse(src)
    fails = []
    if "_origin_guard" not in src or "forbidden origin" not in src:
        fails.append("origin-guard middleware missing")
    if "must be within the try-on workspace" not in src:
        fails.append("render-path containment missing")
    # the containment must resolve against the project root, not a user string
    if "_project_root = Path(__file__).resolve().parent" not in src:
        fails.append("path containment does not anchor to the project root")

    gate = _function_source(tree, "_require_local_secret")
    if not gate:
        fails.append("_require_local_secret missing")
    else:
        if "x-tryon-local-secret" not in gate or "status_code=401" not in gate:
            fails.append("_require_local_secret does not answer 401 on the x-tryon-local-secret header")
        if "not _TRYON_LOCAL_SECRET" not in gate:
            fails.append("_require_local_secret does not fail closed when TRYON_LOCAL_SECRET is unset")
    gated = _secret_gated_paths(tree)
    for route in SECRET_GATED_ROUTES:
        if route not in gated:
            fails.append(f"{route} is not gated by _require_local_secret")

    version = _fastapi_version(tree)
    if not version or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        fails.append(f"FastAPI app version is not X.Y.Z (found {version!r})")

    for f in fails:
        print(f"FAIL {f}")
    if fails:
        return 1
    print(
        "smoke_local_api_fencing: ok  origin guard, path containment, "
        f"401 secret gate on {' + '.join(SECRET_GATED_ROUTES)}, version {version}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
