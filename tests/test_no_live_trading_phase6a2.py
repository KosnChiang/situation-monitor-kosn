"""Phase 6.A-2 mock-only invariants for executor/broker_adapter.py
and executor/executor_router.py.

Mirrors the structural guards in earlier phases, scoped to the two
new in-repo files plus a repo-wide check that no concrete
``LiveBrokerAdapter`` class exists outside docs / tests.
"""
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


PHASE6A2_FILES = [
    ROOT / "executor" / "broker_adapter.py",
    ROOT / "executor" / "executor_router.py",
]


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

FORBIDDEN_FUNCTION_NAMES = [
    "place_order", "submit_order", "send_order",
    "live_order", "real_order", "place_live_order", "execute_live",
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
    r"\bimport\s+requests\b", r"\bfrom\s+requests\b",
    r"\bimport\s+httpx\b",    r"\bfrom\s+httpx\b",
    r"\bimport\s+aiohttp\b",  r"\bfrom\s+aiohttp\b",
    r"urllib\.request",
]

EXECUTION_BYPASS = [
    re.compile(r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


def test_phase6a2_files_exist():
    for p in PHASE6A2_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports_in_phase6a2_files():
    offenders = []
    for path in PHASE6A2_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"broker SDK import in 6.A-2 layer: {offenders}"


def test_no_live_order_function_names_in_phase6a2_files():
    offenders = []
    for path in PHASE6A2_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_FUNCTION_NAMES:
            if re.search(rf"\bdef\s+{name}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"live-order function name in 6.A-2 layer: {offenders}"


def test_no_broker_credential_env_reads_in_phase6a2_files():
    offenders = []
    for path in PHASE6A2_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"forbidden credential env reference in 6.A-2 layer: {offenders}"


def test_no_outbound_http_libraries_in_phase6a2_files():
    offenders = []
    for path in PHASE6A2_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"outbound HTTP library in 6.A-2 layer: {offenders}"


def test_no_execution_bypass_switches_in_phase6a2_files():
    offenders = []
    for path in PHASE6A2_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders, f"execution bypass switch in 6.A-2 layer: {offenders}"


def test_router_refuses_live_trading_env(monkeypatch, tmp_path):
    from executor.executor_router import ExecutorRouter, LiveTradingForbidden
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(log_path=str(tmp_path / "x.jsonl"))


def test_router_refuses_execution_mode_live(monkeypatch, tmp_path):
    from executor.executor_router import ExecutorRouter, LiveTradingForbidden
    monkeypatch.setenv("LIVE_TRADING", "false")
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(execution_mode="live", log_path=str(tmp_path / "x.jsonl"))


def test_paper_adapter_submit_is_not_implemented():
    from executor.broker_adapter import PaperBrokerAdapter
    from strategy.fibo_mob_v2 import Signal
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "test")
    with pytest.raises(NotImplementedError):
        PaperBrokerAdapter().submit(sig)


def test_no_live_broker_adapter_concrete_class_in_source():
    """Phase 6.A-4 introduces LiveBrokerAdapter out-of-tree. In Phase
    6.A-2 the concrete class must NOT exist anywhere in the source
    tree (tests/docs may *mention* the name to enforce this exact rule)."""
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules",
        "__pycache__", ".pytest_cache", "tests", "docs",
    }
    pat = re.compile(r"^class\s+LiveBrokerAdapter\b", re.MULTILINE)
    offenders = []
    for p in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pat.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"LiveBrokerAdapter concrete class found: {offenders}"


def test_router_does_not_import_executor_subclasses_beyond_mock_paper():
    """Router source must only reference MockBrokerAdapter and
    PaperBrokerAdapter from executor.broker_adapter -- no surreptitious
    live adapter import."""
    src = (ROOT / "executor" / "executor_router.py").read_text(encoding="utf-8")
    assert "MockBrokerAdapter" in src
    assert "PaperBrokerAdapter" in src
    assert "LiveBrokerAdapter" not in src


def test_routed_outcome_default_mode_is_mock():
    from executor.executor_router import RoutedOutcome
    out = RoutedOutcome(
        request_id=None, submitted=False, skip_reason="x",
        execution_mode="mock", adapter="mock",
        ts=0.0, timestamp="2026-05-19T00:00:00+00:00",
        symbol=None, side="FLAT", fill_ts=None,
    )
    assert out.mode == "mock"
