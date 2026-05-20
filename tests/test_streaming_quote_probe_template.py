"""Structural tests for templates/shioaji_live_adapter/streaming_quote_probe.py
(Phase 7.B-1).

The probe lives in the template tree and is meant to be copied OUT of the main
repo. These tests do NOT execute the probe (shioaji is not installed in the
main repo venv). They pin its structural contract:

* file exists
* lazy shioaji import (never at module top)
* credential loader present (keyring + env fallback)
* SHIOAJI_SIMULATION refusal path
* sj.Shioaji(simulation=True) hard-pinned at construction
* api.quote.subscribe called
* finite --seconds flag
* JSONL row shape matches main repo's Quote schema
* probe is read-only (no place_order / submit_order)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBE = ROOT / "templates" / "shioaji_live_adapter" / "streaming_quote_probe.py"


def _src() -> str:
    return PROBE.read_text(encoding="utf-8")


def test_probe_file_exists():
    assert PROBE.exists(), f"missing: {PROBE.relative_to(ROOT)}"


def test_probe_imports_shioaji_lazily():
    src = _src()
    assert "import shioaji" in src
    top_pattern = re.compile(
        r"^\s*import\s+shioaji\b|^\s*from\s+shioaji\b", re.MULTILINE,
    )
    for match in top_pattern.finditer(src):
        line_start = src.rfind("\n", 0, match.start()) + 1
        line_end = src.find("\n", match.end())
        line = src[line_start:line_end if line_end != -1 else len(src)]
        assert line.startswith((" ", "\t")), (
            f"top-level shioaji import: {line!r}"
        )


def test_probe_uses_credential_loader():
    src = _src()
    assert "keyring" in src.lower()
    assert "SHIOAJI_API_KEY" in src
    assert "SHIOAJI_SECRET_KEY" in src


def test_probe_enforces_simulation_env():
    src = _src()
    assert "SHIOAJI_SIMULATION" in src
    assert re.search(r"REFUSE.*SHIOAJI_SIMULATION", src), (
        "probe must refuse with explicit message when SHIOAJI_SIMULATION "
        "is not truthy"
    )


def test_probe_pins_simulation_true_at_shioaji_construct():
    src = _src()
    assert re.search(r"sj\.Shioaji\(\s*simulation\s*=\s*True\s*\)", src), (
        "probe must construct Shioaji with simulation=True hard-pinned"
    )


def test_probe_subscribes_via_quote_api():
    src = _src()
    assert "quote.subscribe" in src


def test_probe_has_finite_duration_flag():
    src = _src()
    assert "--seconds" in src
    # Probe must also refuse non-positive durations.
    assert re.search(r"--seconds.*must be > 0", src, re.DOTALL), (
        "probe must reject --seconds <= 0"
    )


def test_probe_writes_jsonl_shape_matches_quote_schema():
    src = _src()
    for key in ("symbol", "bid", "ask", "last", "ts", "timestamp", "source"):
        assert f'"{key}"' in src, f"probe JSONL missing key: {key}"


def test_probe_does_not_call_place_order():
    """A streaming quote probe is read-only and must NOT submit orders.

    Checks for the function-call form ``name(`` so docstrings that
    *mention* the names (in a "this script does not call X" sense) do
    not trip the guard.
    """
    src = _src()
    for forbidden in ("place_order", "submit_order"):
        pat = re.compile(rf"\b{forbidden}\s*\(")
        assert not pat.search(src), (
            f"probe must not call {forbidden}(...)"
        )
