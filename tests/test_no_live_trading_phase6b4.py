"""Phase 6.B-4 structural guards on the wired loader + pipeline."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"


CLI_SRC_PATH = ROOT / "tools" / "dry_run_live_order.py"
PIPELINE_SRC_PATH = ROOT / "live" / "pipeline.py"


FORBIDDEN_IMPORT_PATTERNS = [
    r"\bimport\s+ib_insync\b", r"\bfrom\s+ib_insync\b",
    r"\bimport\s+ibapi\b",     r"\bfrom\s+ibapi\b",
    r"\bimport\s+alpaca\b",    r"\bfrom\s+alpaca\b",
    r"\bimport\s+ccxt\b",      r"\bfrom\s+ccxt\b",
    r"\bimport\s+binance\b",   r"\bfrom\s+binance\b",
    r"\bimport\s+oandapyV20\b", r"\bfrom\s+oandapyV20\b",
    r"\bimport\s+MetaTrader5\b", r"\bfrom\s+MetaTrader5\b",
    r"\bimport\s+shioaji\b",   r"\bfrom\s+shioaji\b",
]

FORBIDDEN_ENV_NAMES = [
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET", "SHIOAJI_TOKEN",
    "IB_USER", "IB_PASSWORD", "IB_ACCOUNT", "IB_GATEWAY_TOKEN",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_KEY", "ALPACA_SECRET", "ALPACA_API_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN", "CTPRO_ACCOUNT",
    "TRADINGVIEW_SESSION", "TRADINGVIEW_AUTH",
]

FORBIDDEN_OUTBOUND_HTTP = [
    r"^\s*import\s+requests\b", r"^\s*from\s+requests\b",
    r"^\s*import\s+httpx\b",    r"^\s*from\s+httpx\b",
    r"^\s*import\s+aiohttp\b",  r"^\s*from\s+aiohttp\b",
    r"^\s*from\s+urllib\.request\b",
]

EXECUTION_BYPASS_NON_LIVE = [
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


# ---------- CLI structural integrity ------------------------------------

def test_cli_has_no_broker_sdk_imports():
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    for pat in FORBIDDEN_IMPORT_PATTERNS:
        assert not re.search(pat, src), f"CLI contains forbidden import: {pat}"


def test_cli_has_no_broker_credential_env_reads():
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    for name in FORBIDDEN_ENV_NAMES:
        assert not re.search(rf"\b{re.escape(name)}\b", src), name


def test_cli_has_no_outbound_http_at_module_top():
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    for pat in FORBIDDEN_OUTBOUND_HTTP:
        assert not re.search(pat, src, re.MULTILINE), pat


def test_cli_has_no_non_live_trading_bypass_switches():
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    for line in src.splitlines():
        for rx in EXECUTION_BYPASS_NON_LIVE:
            assert not rx.search(line), f"CLI bypass switch on line: {line[:80]}"


def test_cli_default_adapter_source_is_fake():
    """The CLI's --adapter-source default MUST be 'fake'. A regression
    here would silently make existing tests start invoking the loader."""
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    # Look for default="fake" near the --adapter-source declaration.
    assert re.search(r"--adapter-source", src)
    assert re.search(r'default="fake"', src), (
        "CLI --adapter-source default must be 'fake' for safety"
    )


def test_cli_external_branch_routes_through_loader():
    """The CLI must invoke resolve_live_adapter only on the external
    branch -- not from any other code path."""
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    assert "resolve_live_adapter()" in src
    # Single call site:
    assert src.count("resolve_live_adapter()") == 1


# ---------- Pipeline still independent of the loader --------------------

def test_pipeline_does_not_import_adapter_loader():
    """Pipeline orchestration must remain orthogonal to adapter
    resolution. The CLI picks the adapter; the pipeline just uses it."""
    src = PIPELINE_SRC_PATH.read_text(encoding="utf-8")
    assert "adapter_loader" not in src
    assert "resolve_live_adapter" not in src


def test_pipeline_adapter_annotation_uses_protocol():
    """The pipeline's constructor must type-hint the Protocol, so any
    Protocol-conforming adapter (fake or path-loaded) can be passed."""
    src = PIPELINE_SRC_PATH.read_text(encoding="utf-8")
    assert "LiveBrokerAdapterProtocol" in src
    # The annotation pattern is "adapter: LiveBrokerAdapterProtocol".
    assert re.search(
        r"adapter:\s*LiveBrokerAdapterProtocol", src,
    ), "pipeline constructor must annotate adapter with the Protocol"


# ---------- Exit code 8 reserved for adapter load failure --------------

def test_cli_uses_exit_code_8_for_adapter_load_failure():
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    # Exit 8 must appear in the CLI source as the adapter-load failure
    # code. We look for "return 8" near the LiveAdapterLoadError handler.
    assert re.search(
        r"except\s+LiveAdapterLoadError[\s\S]{0,200}return\s+8",
        src,
    ), "CLI must return exit 8 on adapter load failure"


# ---------- Kill switch pre-check fires BEFORE adapter resolution ------

def test_cli_kill_switch_precheck_before_adapter_resolution():
    """The kill-switch pre-check must appear in the CLI source BEFORE
    the adapter resolution branch. A regression here would mean a
    killed session still triggers an external adapter load (and
    possibly a broker connection in operator-supplied plugins)."""
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    pre_check_idx = src.find("pre_ks")
    resolve_idx = src.find("resolve_live_adapter()")
    assert pre_check_idx != -1, "pre_ks variable not found in CLI"
    assert resolve_idx != -1, "resolve_live_adapter() call not found in CLI"
    assert pre_check_idx < resolve_idx, (
        "kill switch pre-check must occur before resolve_live_adapter()"
    )


# ---------- Runtime invariants ------------------------------------------

def test_cli_fake_branch_does_not_call_resolver():
    """Static source check: inside the ``if args.adapter_source == "fake"``
    block, no call to resolve_live_adapter must appear. The single call
    site is inside the ``else`` (external) branch.

    The runtime equivalent of this guarantee (audit log is empty when
    fake-default is used) is covered by
    tests/test_dry_run_live_order_external.py::test_default_fake_mode_does_not_invoke_loader."""
    src = CLI_SRC_PATH.read_text(encoding="utf-8")
    if_idx = src.find('args.adapter_source == "fake"')
    assert if_idx != -1, "fake branch marker not found"
    # The matching `else:` for that if-block starts on its own line.
    else_idx = src.find("\n    else:", if_idx)
    assert else_idx != -1, "else branch not found after fake branch"
    fake_body = src[if_idx:else_idx]
    assert "resolve_live_adapter(" not in fake_body, (
        "fake branch must not call resolve_live_adapter"
    )
