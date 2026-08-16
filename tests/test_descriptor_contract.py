"""Cross-repo QueryDescriptor contract test — the QueryMe arm (ADR 013 §6 RC5).

QueryMe owns the ``QueryDescriptor`` schema, so this arm is the reference
point the other two representations are pinned against:

1. **QueryMe** ``QueryDescriptor`` Pydantic model (this file) — the golden
   must validate and round-trip through it.
2. **Blue's preview executor** ``core.db.*`` builder chain
   (``Blue/tests/test_querydescriptor_contract.py``) — pinned against a
   byte-identical copy of the same golden.
3. **Orion** ``queryDescriptor`` Go struct
   (``Orion/internal/runtime/compute_db_test.go::TestQueryDescriptor_GoldenParity``)
   — pinned against its own byte-identical copy.

A field rename or reorder on any side breaks its own arm loudly, so the
three representations can never silently drift apart.

QueryMe does not consume the ``blue-runtime-go`` module (``pyproject.toml``
has no path into it — Python cannot import a Go artefact), so this arm has
no digest-pinned authenticity channel back to Blue's published fixture
table: it is the ADR 013 G7 case, its inventory entry is **attested**, not
verified, and this test is the whole of its local control. The one thing
it *can* check on its own is that the copy of the golden it ships has not
drifted from the digest recorded alongside it
(``tests/fixtures/querydescriptor_golden.json.sha256``) — the same
self-consistency channel §3.4.1 gives every implementer, computed here
rather than trusted from a hand-copied literal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from queryme.descriptor import QueryDescriptor

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_GOLDEN_PATH = _FIXTURES_DIR / "querydescriptor_golden.json"
_SIDECAR_PATH = _FIXTURES_DIR / "querydescriptor_golden.json.sha256"

_GOLDEN_BYTES = _GOLDEN_PATH.read_bytes()
_GOLDEN = json.loads(_GOLDEN_BYTES)


def _canonical(d: dict[str, Any]) -> dict[str, Any]:
    """Normalise a descriptor dict for cross-arm comparison: drop a
    top-level ``offset`` that is absent/None. The golden carries no
    ``offset`` key at all; Pydantic fills the field with its ``None``
    default on validation, so a direct dict comparison would fail on that
    field alone without touching the contract this test actually guards.
    """
    return {k: v for k, v in d.items() if not (k == "offset" and v is None)}


def test_golden_fixture_matches_its_sidecar_digest() -> None:
    """Local self-consistency channel (§3.4.1): the copy of the golden this
    repo ships has not drifted from the digest recorded next to it. This is
    QueryMe's only local control — it has no module-pinned digest to check
    against (G7) — so the sidecar must be produced from the real file, not
    hand-copied, or this test would pass while proving nothing.
    """
    want = hashlib.sha256(_GOLDEN_BYTES).hexdigest()
    got_line = _SIDECAR_PATH.read_text(encoding="utf-8").strip()
    got = got_line.split()[0]
    assert got == want, f"sidecar records {got}, golden file actually hashes to {want}"


def test_queryme_model_round_trips_golden() -> None:
    """The schema owner accepts the golden and re-emits the same shape."""
    desc = QueryDescriptor.model_validate(_GOLDEN)
    dumped = desc.model_dump(mode="json")
    assert _canonical(dumped) == _canonical(_GOLDEN)
