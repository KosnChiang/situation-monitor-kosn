"""Structural tests for the Shioaji adapter TEMPLATE (Phase 7.A / 7.A-2).

The template lives at templates/shioaji_live_adapter/ and is meant to
be COPIED OUT of the main repo. These tests check the template's
shape but do NOT call into shioaji (the SDK is not installed in this
repo's venv). One executable test loads the module and exercises
``_resolve_tmf_contract`` against a fake api object.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


TEMPLATE_DIR = ROOT / "templates" / "shioaji_live_adapter"
TEMPLATE_ADAPTER = TEMPLATE_DIR / "live_adapter.py"
TEMPLATE_README = TEMPLATE_DIR / "README.md"
TEMPLATE_REQS = TEMPLATE_DIR / "requirements.txt"


def _load_template_module():
    spec = importlib.util.spec_from_file_location(
        "shioaji_live_adapter_template", TEMPLATE_ADAPTER
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _adapter_with_api(api):
    """Construct a LiveAdapter without running __init__ (which would
    demand credentials), then attach a fake api."""
    mod = _load_template_module()
    adapter = mod.LiveAdapter.__new__(mod.LiveAdapter)
    adapter._api = api
    return adapter


def test_template_dir_exists():
    assert TEMPLATE_DIR.is_dir()


def test_template_files_exist():
    assert TEMPLATE_ADAPTER.exists()
    assert TEMPLATE_README.exists()
    assert TEMPLATE_REQS.exists()


def test_template_defines_class_liveadapter():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "class LiveAdapter:" in src


def test_template_imports_shioaji_inside_methods_only():
    """The template must use lazy imports for shioaji so the file
    loads (e.g. for linting) without the SDK installed. No top-level
    'import shioaji'."""
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "import shioaji" in src
    # Make sure shioaji isn't imported at the module top:
    top_pattern = re.compile(r"^\s*import\s+shioaji\b|^\s*from\s+shioaji\b", re.MULTILINE)
    for match in top_pattern.finditer(src):
        line_start = src.rfind("\n", 0, match.start()) + 1
        # If the line has any leading whitespace, it's indented (i.e.
        # inside a function); top-level imports start at column 0.
        line = src[line_start:src.find("\n", match.end())]
        assert line.startswith((" ", "\t")), (
            f"shioaji must not be imported at module top: {line!r}"
        )


def test_template_uses_keyring_for_credentials():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "keyring" in src.lower()


def test_template_supports_env_fallback_for_credentials():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "SHIOAJI_API_KEY" in src
    assert "SHIOAJI_SECRET_KEY" in src


def test_template_supports_simulation_toggle():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "SHIOAJI_SIMULATION" in src


def test_template_resolves_tmf_contract_dynamically():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    # Must enumerate Contracts.Futures and pick by code/name match.
    assert "Contracts.Futures" in src
    assert "TMF" in src or "微型台指" in src


def test_template_prefers_attribute_access_for_tmf():
    """Phase 7.A-2: the resolver must try the TMF sub-namespace and
    TMFR1 alias before falling back to iteration. Iteration alone
    returns nothing on shioaji 1.3.x paper sessions."""
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "Contracts.Futures" in src
    assert "TMF" in src
    assert "TMFR1" in src, "resolver must prefer the TMFR1 front-month alias"
    # Lexical YYYYMM fallback comment / pattern present.
    assert "TMFYYYYMM" in src or "YYYYMM" in src


def _make_tmf_namespace(*names):
    ns = SimpleNamespace()
    for n in names:
        setattr(ns, n, SimpleNamespace(code=n))
    return ns


def _make_api(tmf_ns=None, iterable_futures=None):
    futures = SimpleNamespace()
    if tmf_ns is not None:
        futures.TMF = tmf_ns
    if iterable_futures is not None:
        # Replace the SimpleNamespace with an iterable wrapper that
        # still allows attribute access to TMF.
        class _Futures:
            def __init__(self, items, tmf):
                self._items = items
                if tmf is not None:
                    self.TMF = tmf
            def __iter__(self):
                return iter(self._items)
        futures = _Futures(iterable_futures, tmf_ns)
    contracts = SimpleNamespace(Futures=futures)
    return SimpleNamespace(Contracts=contracts)


def test_resolver_prefers_tmfr1_when_present():
    tmf = _make_tmf_namespace(
        "TMFR1", "TMFR2", "TMF202605", "TMF202606", "TMF202703",
    )
    adapter = _adapter_with_api(_make_api(tmf_ns=tmf))
    contract = adapter._resolve_tmf_contract()
    assert contract.code == "TMFR1"


def test_resolver_falls_back_to_nearest_dated_when_no_tmfr1():
    tmf = _make_tmf_namespace(
        "TMF202703", "TMF202612", "TMF202605", "TMF202609",
    )
    adapter = _adapter_with_api(_make_api(tmf_ns=tmf))
    contract = adapter._resolve_tmf_contract()
    # Lexical sort of YYYYMM == calendar order; earliest = nearest.
    assert contract.code == "TMF202605"


def test_resolver_ignores_non_dated_attrs_in_tmf_ns():
    # Real shioaji namespace may carry helper attrs beyond contracts.
    tmf = SimpleNamespace()
    tmf.TMF202609 = SimpleNamespace(code="TMF202609")
    tmf.TMF202612 = SimpleNamespace(code="TMF202612")
    tmf.helper_method = lambda: None  # not a contract
    tmf.TMFOO = SimpleNamespace(code="TMFOO")  # malformed suffix, must be skipped
    adapter = _adapter_with_api(_make_api(tmf_ns=tmf))
    contract = adapter._resolve_tmf_contract()
    assert contract.code == "TMF202609"


def test_resolver_falls_back_to_iteration_when_no_tmf_namespace():
    """Legacy SDK shape: no TMF sub-namespace, futures iterable."""
    iterable = [
        SimpleNamespace(code="TMF202606", name="", delivery_month="202606"),
        SimpleNamespace(code="TMF202605", name="", delivery_month="202605"),
        SimpleNamespace(code="TXFA5", name="台指期", delivery_month="202605"),
    ]
    adapter = _adapter_with_api(_make_api(iterable_futures=iterable))
    contract = adapter._resolve_tmf_contract()
    assert contract.code == "TMF202605"


def test_resolver_raises_when_nothing_found():
    """No TMF namespace and iteration yields nothing."""
    adapter = _adapter_with_api(_make_api(iterable_futures=[]))
    with pytest.raises(RuntimeError, match="TMF"):
        adapter._resolve_tmf_contract()


def test_resolver_handles_non_iterable_futures_container():
    """shioaji 1.3.x paper: Contracts.Futures is not iterable but has
    no TMF attribute either -- must raise cleanly, not propagate
    TypeError."""
    futures = SimpleNamespace()  # no TMF, not iterable
    api = SimpleNamespace(Contracts=SimpleNamespace(Futures=futures))
    adapter = _adapter_with_api(api)
    with pytest.raises(RuntimeError, match="TMF"):
        adapter._resolve_tmf_contract()


def test_template_protocol_methods_present():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    for m in ("def connect", "def disconnect", "def submit_order",
              "def cancel_order", "def positions", "def account_equity"):
        assert m in src, f"template missing method: {m}"


def test_requirements_lists_shioaji():
    req = TEMPLATE_REQS.read_text(encoding="utf-8").lower()
    assert "shioaji" in req


def test_readme_documents_copy_path():
    readme = TEMPLATE_README.read_text(encoding="utf-8")
    assert "C:\\Trading\\live-adapters" in readme or "live-adapters" in readme
    assert "LIVE_BROKER_ADAPTER_PATH" in readme
