# Code Comment Standard

What a comment in this repository is for, and how to check the tree against that.

This exists because two audits found the same defect class: comments describing code
that no longer runs. Twice the false claim was about a *safety* behavior — "hands are
always preserved", "the VAE is float32 on MPS" — which is the expensive kind, because
it stops a reader from investigating the thing that is actually broken.

## The rules

Adopted from [Google's Python style guide §3.8](https://google.github.io/styleguide/pyguide.html)
and [PEP 257](https://peps.python.org/pep-0257/), with one addition of our own.

1. **A docstring is required** when a function is part of a cross-module API, is
   nontrivial in size, or has non-obvious logic. Private one-liners do not need one.
2. **Never describe the code.** Assume the reader knows Python. Say why, not what.
   `# Hardware selection` above `device = _preferred_device()` earns nothing.
3. **Document the contract**: what it returns, what it raises, what it mutates, and
   any ordering or locking the caller must respect.
4. **Prescriptive mood** — "Return the seed list", not "Returns the seed list".
5. **Comment the trap, not the obvious.** The thresholds that came from tuning, the
   prompt text that is load-bearing, the argument that must be the models root and not
   the app root — those are what a comment is for.
6. **Ours: a comment must be checkable from where it sits.** Prefer naming a symbol
   over a line number. `see self.vae_dtype in vendor/CatVTON/model/pipeline.py` survives
   edits; `pipeline.py:60` silently rots. The research below is why this rule exists.

## Why rule 6

Wen et al. mined 1.3 billion AST-level changes across 1,500 systems
([ICPC 2019](https://dl.acm.org/doi/abs/10.1109/ICPC.2019.00019)). Two findings drive
our practice:

- Where a comment is updated with its code at all, **97%** of the time it happens in the
  *same commit*. A comment that is not updated alongside the change is, in practice,
  never updated.
- Changes that leave code and comment inconsistent are about **1.5× more likely** to be
  part of a bug-introducing commit.

So the risk is not "comments drift slowly". It is: whatever a comment describes that is
more than one edit away from the comment is already unreliable. That makes cross-file
references, duplicated constants, and claims about a *caller's* behavior the highest-risk
forms — write those so a grep can catch them.

## Dead code must say so

When a code path is disabled by a hard override, the comment on that path says so and
names the override. Both P1 findings so far were this exact shape: a real block, real
tests, a comment in the present tense, and a flag two hundred lines up that made it all
unreachable.

If a path is disabled, the comment states three things: that it does not run, what
disables it, and what to do instead — either how to re-enable it, or where the behavior
really comes from now.

## Checking the tree

Both scripts are throwaway; paste them into a scratch file when you want the numbers.

Coverage against rule 1 — every undocumented definition that meets a trigger, ranked:

```python
import ast
from pathlib import Path

root = Path(".")
skip = {"vendor", ".venv311", "node_modules", "__pycache__"}
BRANCH = (ast.If, ast.For, ast.While, ast.Try, ast.ExceptHandler, ast.BoolOp, ast.comprehension)
for f in sorted(p for p in root.rglob("*.py") if not any(s in p.parts for s in skip)):
    tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    for n in ast.walk(tree):
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) or ast.get_docstring(n):
            continue
        loc = (n.end_lineno or n.lineno) - n.lineno + 1
        cx = sum(1 for x in ast.walk(n) if isinstance(x, BRANCH))
        if loc >= 25 or cx >= 8 or not n.name.startswith("_"):
            print(f"{f}:{n.lineno} {n.name} loc={loc} cx={cx}")
```

Config drift — documented environment variables the code never reads:

```python
import re
from pathlib import Path

code = set()
for p in list(Path(".").rglob("*.py")) + list(Path(".").rglob("*.sh")):
    if any(s in p.parts for s in {"vendor", ".venv311", "node_modules"}):
        continue
    t = p.read_text(encoding="utf-8", errors="replace")
    code |= set(re.findall(r'os\.(?:getenv|environ\.get)\(\s*["\']([A-Z0-9_]+)["\']', t))
    code |= set(re.findall(r'os\.environ\[["\']([A-Z0-9_]+)["\']\]', t))
documented = set(re.findall(r"^([A-Z0-9_]+)=", Path(".env.tryon-worker.example").read_text(), re.M))
print("documented but never read:", sorted(documented - code))
```

That second one is how `TRYON_POLL_INTERVAL_SECONDS` was caught: documented in two
places, read nowhere, and defaulting to the same value as the setting it appeared to
control — so an operator changing it would see no effect and no error.

## Verifying a comment-only change

Comment edits must not change behavior, and that is worth proving rather than assuming:

```python
import ast, subprocess

def strip(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                n.body = b[1:] or [ast.Pass()]
    return tree

for f in ("app.py", "scripts/tryon_queue_worker.py"):
    old = subprocess.run(["git", "show", f"HEAD:{f}"], capture_output=True, text=True).stdout
    same = ast.dump(strip(ast.parse(old))) == ast.dump(strip(ast.parse(open(f).read())))
    print(("IDENTICAL " if same else "CODE CHANGED ") + f)
```

## Scope

Applies to first-party code: `app.py`, `services/`, `scripts/`, `studio_tools/`,
`model_paths.py`, `warp_repair.py`, `tests/`.

`vendor/` is upstream CatVTON and is exempt — do not restyle its comments. When our
code depends on vendored behavior, document it on **our** side, naming the vendored
symbol, since we cannot rely on an upstream comment staying put.
