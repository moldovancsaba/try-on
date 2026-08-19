"""Smoke test for two-pass outfit orchestration (try-on#39).

Offline: no database, no local API, no model. Builds a bare TryOnWorker-like
object (the real class needs Mongo to construct, so the method under test is
bound onto a stub) with a fake suits collection and a fake local-API call
that writes marker files. Verifies: the fixed top-then-bottom pass order and
per-pass payload shaping; all named fail-fast validations
(outfit_requires_local_provider, outfit_top_type_mismatch,
outfit_bottom_type_mismatch, same-id pairing); atomicity on pass-2 failure
(no result produced); and intermediate cleanup on every exit path.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from tryon_queue_worker import TryOnQueueWorker, classify_failure  # noqa: E402


class _FakeSuits:
    def __init__(self, docs):
        self._docs = {d["leatherSuitId"]: d for d in docs}

    def find_one(self, query):
        doc = self._docs.get(query.get("leatherSuitId"))
        if doc and query.get("active") is True and not doc.get("active", True):
            return None
        return doc


class _StubWorker:
    """Bears only what _run_outfit_passes touches (the real class needs Mongo)."""

    _run_outfit_passes = TryOnQueueWorker._run_outfit_passes

    def __init__(self, suits, *, api_fail_on_pass: int | None = None):
        self.suits = suits
        self.calls: list[dict] = []
        self.heartbeats = 0
        self._api_fail_on_pass = api_fail_on_pass

    def local_tryon_api_is_ready(self):
        return True

    def heartbeat(self, job_id):
        self.heartbeats += 1

    def stage_suit_asset(self, suit_id, destination: Path):
        destination.write_bytes(b"garment:" + suit_id.encode())
        return f"staged:{suit_id}"

    def call_local_tryon_api(self, person_path: Path, garment_path: Path, output_path: Path, payload: dict):
        pass_no = len(self.calls) + 1
        self.calls.append(
            {
                "pass": pass_no,
                "person": str(person_path),
                "garment": str(garment_path),
                "output": str(output_path),
                "category": payload.get("category"),
                "mask_mode": payload.get("mask_mode"),
                "sleeve_length": payload.get("sleeve_length"),
                "category_source": payload.get("category_source"),
            }
        )
        if self._api_fail_on_pass == pass_no:
            raise RuntimeError("local_tryon_api_failed:500:boom")
        output_path.write_bytes(b"render-pass-%d" % pass_no)
        return {"status": "succeeded", "output_image_path": str(output_path)}


SUITS = [
    {"leatherSuitId": "top_home_v1", "garmentType": "top", "active": True},
    {"leatherSuitId": "bottom_home_v1", "garmentType": "bottom", "active": True},
    {"leatherSuitId": "jersey_v1", "garmentType": "jersey", "active": True},
]


def _job(top="top_home_v1", bottom="bottom_home_v1"):
    return {"jobId": "job_test", "request": {"leatherSuitId": top, "outfitBottomLeatherSuitId": bottom}}


def _run(worker: _StubWorker, workspace: Path, *, profile="motogp_leather_magic", job=None, payload=None):
    person = workspace / "person.jpg"
    person.write_bytes(b"person")
    top = workspace / "top.png"
    top.write_bytes(b"top")
    result = workspace / "result.png"
    return worker._run_outfit_passes(
        job_id="job_test",
        job=job or _job(),
        payload=payload or {"processing_profile": profile, "mask_mode": "expose_arms", "sleeve_length": "default"},
        processing_profile=profile,
        person_input_path=person,
        top_input_path=top,
        workspace_root=workspace,
        result_path=result,
        bottom_suit_id=(job or _job())["request"]["outfitBottomLeatherSuitId"],
    ), result


def main() -> int:
    failures: list[str] = []

    # --- happy path: order, chaining, payload shaping, cleanup, result ---
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        worker = _StubWorker(_FakeSuits(SUITS))
        _, result = _run(worker, ws)
        if len(worker.calls) != 2:
            failures.append(f"expected 2 passes, got {len(worker.calls)}")
        else:
            p1, p2 = worker.calls
            if p1["category"] != "upper" or p2["category"] != "lower":
                failures.append(f"pass order/categories wrong: {p1['category']}, {p2['category']}")
            if p2["person"] != p1["output"]:
                failures.append("pass 2 must consume pass 1's output as its person image")
            if p1["mask_mode"] != "expose_arms":
                failures.append("pass 1 must inherit the payload's mask_mode (sleeveless top composes with expose_arms)")
            if p2["mask_mode"] != "default" or p2["sleeve_length"] != "default":
                failures.append("pass 2 must reset mask_mode/sleeve_length (no arm concerns on a bottom)")
            if p1["category_source"] != "garment_type" or p2["category_source"] != "garment_type":
                failures.append("both passes must mark category_source=garment_type so the profile can't stomp them")
        if not result.exists() or result.read_bytes() != b"render-pass-2":
            failures.append("final result must be pass 2's output")
        if (ws / "outfit_pass1.png").exists():
            failures.append("intermediate pass-1 image must be cleaned up on success")
        if worker.heartbeats < 1:
            failures.append("a heartbeat must fire between the passes")

    # --- atomicity: pass-2 failure produces no result, cleans intermediate, raises ---
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        worker = _StubWorker(_FakeSuits(SUITS), api_fail_on_pass=2)
        try:
            _run(worker, ws)
            failures.append("pass-2 failure must raise")
        except RuntimeError:
            pass
        if (ws / "result.png").exists():
            failures.append("pass-2 failure must not leave a result file")
        if (ws / "outfit_pass1.png").exists():
            failures.append("intermediate must be cleaned on failure too")

    # --- named validations, each terminal per classify_failure ---
    cases = [
        ("outfit_requires_local_provider", dict(profile="fal_tryon")),
        ("outfit_top_type_mismatch", dict(job=_job(top="jersey_v1"))),
        ("outfit_bottom_type_mismatch", dict(job=_job(bottom="jersey_v1"))),
        ("outfit_bottom_type_mismatch", dict(job=_job(top="top_home_v1", bottom="top_home_v1"))),  # same-id pairing
        ("outfit_bottom_type_mismatch", dict(job=_job(bottom="missing_entirely"))),
    ]
    for expected_code, kwargs in cases:
        with tempfile.TemporaryDirectory() as tmp:
            worker = _StubWorker(_FakeSuits(SUITS))
            try:
                _run(worker, Path(tmp), **kwargs)
                failures.append(f"{expected_code}: expected a raise, got success")
                continue
            except RuntimeError as exc:
                if expected_code not in str(exc):
                    failures.append(f"expected {expected_code}, got {exc}")
                retryable, category = classify_failure(str(exc))
                if retryable or category != "invalid_job_contract":
                    failures.append(f"{expected_code}: must classify terminal invalid_job_contract, got ({retryable}, {category})")
            if worker.calls:
                failures.append(f"{expected_code}: validation must fail before any render spend, got {len(worker.calls)} calls")

    for line in failures:
        print(f"FAIL {line}")
    if failures:
        return 1
    print("smoke_outfit_orchestration: ok  order, chaining, payload shaping, atomicity, cleanup, all named validations terminal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
