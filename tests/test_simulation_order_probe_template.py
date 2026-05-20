"""Structural tests for templates/shioaji_live_adapter/simulation_order_probe.py
(Phase 7.C).

The probe lives in the template tree. These tests do NOT execute it. They pin:

* file exists
* lazy shioaji import (never at module top)
* credential loader present (keyring + env fallback)
* SHIOAJI_SIMULATION refusal path
* sj.Shioaji(simulation=True) hard-pinned at construction
* DRY-RUN IS DEFAULT: --confirm-simulation-submit (store_true) required for
  api.place_order; the confirm check appears in source BEFORE the place_order
  call
* IOC default order type
* --cancel-after present + api.cancel_order called
* writes audit JSONL with both intent and submit records

Phase 7.A-2 follow-up: also exercises ``_resolve_tmf_contract`` behavior
against fake api objects (TMFR1 preference, dated YYYYMM fallback,
legacy iteration, raise-on-empty, --code request path).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROBE = ROOT / "templates" / "shioaji_live_adapter" / "simulation_order_probe.py"


def _src() -> str:
    return PROBE.read_text(encoding="utf-8")


def _load_probe_module():
    """Load simulation_order_probe.py as a module without running main().

    The probe's module top is SDK-free (lazy ``import shioaji`` lives
    inside ``main()``), so ``exec_module`` succeeds even though shioaji
    is not installed in this venv.
    """
    spec = importlib.util.spec_from_file_location(
        "simulation_order_probe_template", PROBE,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def test_probe_default_is_dry_run():
    """--confirm-simulation-submit must be present and store_true so
    argparse defaults to False (dry-run)."""
    src = _src()
    assert "--confirm-simulation-submit" in src
    flag_pattern = re.compile(
        r"--confirm-simulation-submit[\"'][^)]*?"
        r"action\s*=\s*[\"']store_true[\"']",
        re.DOTALL,
    )
    assert flag_pattern.search(src), (
        "--confirm-simulation-submit must use action='store_true' so its "
        "default is False"
    )


def test_probe_guards_place_order_with_confirm_flag():
    """The confirm flag must be checked in source order BEFORE the
    place_order call -- a typo or omission must not fire the order.

    Uses the function-call pattern ``api.place_order(`` and the
    code-form attribute ``args.confirm_simulation_submit`` to skip
    docstring mentions.
    """
    src = _src()
    call_match = re.search(r"\bapi\.place_order\s*\(", src)
    guard_match = re.search(r"\bargs\.confirm_simulation_submit\b", src)
    assert call_match is not None, "probe must call api.place_order(...)"
    assert guard_match is not None, (
        "probe must read args.confirm_simulation_submit before the call"
    )
    assert guard_match.start() < call_match.start(), (
        "args.confirm_simulation_submit must appear in source before "
        "api.place_order(...) so the call is gated by the flag"
    )


def test_probe_ioc_default():
    src = _src()
    assert re.search(
        r"--order-type[\"'].*?default\s*=\s*[\"']IOC[\"']", src, re.DOTALL,
    ), "--order-type must default to IOC"


def test_probe_supports_cancel_after():
    src = _src()
    assert "--cancel-after" in src
    assert "api.cancel_order" in src


def test_probe_writes_audit_jsonl():
    src = _src()
    assert ".jsonl" in src
    # The probe must log both an intent record and a submit record.
    assert '"kind": "intent"' in src, "missing intent record"
    assert '"kind": "submit"' in src, "missing submit record"


# ---------------------------------------------------------------------------
# Phase 7.A-2: behavioral tests for _resolve_tmf_contract
# ---------------------------------------------------------------------------

def _make_tmf_namespace(*codes):
    ns = SimpleNamespace()
    for c in codes:
        setattr(ns, c, SimpleNamespace(code=c))
    return ns


def _make_api(tmf_ns=None, iterable_futures=None):
    """Build a fake api object whose Contracts.Futures matches the
    shape we want to exercise.

    * ``tmf_ns`` attaches a TMF sub-namespace (the shioaji 1.3.x shape).
    * ``iterable_futures`` makes Contracts.Futures iterable over the
      given list (the legacy SDK shape).
    """
    futures = SimpleNamespace()
    if tmf_ns is not None:
        futures.TMF = tmf_ns
    if iterable_futures is not None:
        class _Futures:
            def __init__(self, items, tmf):
                self._items = items
                if tmf is not None:
                    self.TMF = tmf
            def __iter__(self):
                return iter(self._items)
        futures = _Futures(iterable_futures, tmf_ns)
    return SimpleNamespace(Contracts=SimpleNamespace(Futures=futures))


def test_resolver_prefers_tmfr1_when_no_requested_code():
    mod = _load_probe_module()
    tmf = _make_tmf_namespace(
        "TMFR1", "TMFR2", "TMF202605", "TMF202606", "TMF202703",
    )
    api = _make_api(tmf_ns=tmf)
    contract = mod._resolve_tmf_contract(api, None)
    assert contract.code == "TMFR1"


def test_resolver_falls_back_to_nearest_dated_when_no_tmfr1():
    mod = _load_probe_module()
    tmf = _make_tmf_namespace(
        "TMF202703", "TMF202612", "TMF202605", "TMF202609",
    )
    api = _make_api(tmf_ns=tmf)
    contract = mod._resolve_tmf_contract(api, None)
    assert contract.code == "TMF202605"


def test_resolver_ignores_non_dated_attrs_in_tmf_ns():
    mod = _load_probe_module()
    tmf = SimpleNamespace()
    tmf.TMF202609 = SimpleNamespace(code="TMF202609")
    tmf.TMF202612 = SimpleNamespace(code="TMF202612")
    tmf.helper_method = lambda: None
    tmf.TMFOO = SimpleNamespace(code="TMFOO")
    api = _make_api(tmf_ns=tmf)
    contract = mod._resolve_tmf_contract(api, None)
    assert contract.code == "TMF202609"


def test_resolver_falls_back_to_iteration_when_no_tmf_namespace():
    mod = _load_probe_module()
    iterable = [
        SimpleNamespace(code="TMF202606", name="", delivery_month="202606"),
        SimpleNamespace(code="TMF202605", name="", delivery_month="202605"),
        SimpleNamespace(code="TXFA5", name="台指期", delivery_month="202605"),
    ]
    api = _make_api(iterable_futures=iterable)
    contract = mod._resolve_tmf_contract(api, None)
    assert contract.code == "TMF202605"


def test_resolver_raises_when_nothing_found():
    mod = _load_probe_module()
    api = _make_api(iterable_futures=[])
    with pytest.raises(RuntimeError, match="TMF"):
        mod._resolve_tmf_contract(api, None)


def test_resolver_handles_non_iterable_futures_container():
    mod = _load_probe_module()
    futures = SimpleNamespace()  # no TMF, not iterable
    api = SimpleNamespace(Contracts=SimpleNamespace(Futures=futures))
    with pytest.raises(RuntimeError, match="TMF"):
        mod._resolve_tmf_contract(api, None)


def test_resolver_returns_requested_code_via_attribute():
    mod = _load_probe_module()
    tmf = _make_tmf_namespace("TMFR1", "TMFR2", "TMF202605")
    api = _make_api(tmf_ns=tmf)
    contract = mod._resolve_tmf_contract(api, "TMFR2")
    assert contract.code == "TMFR2"


def test_resolver_returns_requested_code_via_iteration_when_no_tmf_ns():
    mod = _load_probe_module()
    iterable = [
        SimpleNamespace(code="TMF202605", name="", delivery_month="202605"),
        SimpleNamespace(code="TMF202607", name="", delivery_month="202607"),
    ]
    api = _make_api(iterable_futures=iterable)
    contract = mod._resolve_tmf_contract(api, "TMF202607")
    assert contract.code == "TMF202607"


def test_resolver_raises_when_requested_code_not_found():
    mod = _load_probe_module()
    tmf = _make_tmf_namespace("TMFR1", "TMF202605")
    api = _make_api(tmf_ns=tmf)
    with pytest.raises(RuntimeError, match="TMFX"):
        mod._resolve_tmf_contract(api, "TMFX")
