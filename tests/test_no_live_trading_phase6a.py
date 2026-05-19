"""Phase 6.A-1 mock-only invariants for the approval/ layer.

Mirrors the structural guards in test_mock_only.py and
test_no_live_trading_phase6.py, scoped to the four files Phase 6.A-1
introduces:

  * approval/__init__.py
  * approval/models.py
  * approval/approval_gate.py
  * tools/mock_approval_flow.py
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


PHASE6A_FILES = [
    ROOT / "approval" / "__init__.py",
    ROOT / "approval" / "models.py",
    ROOT / "approval" / "approval_gate.py",
    ROOT / "tools" / "mock_approval_flow.py",
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


def test_phase6a_files_exist():
    for p in PHASE6A_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports_in_phase6a_files():
    offenders = []
    for path in PHASE6A_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"broker SDK import in approval layer: {offenders}"


def test_no_live_order_function_names_in_phase6a_files():
    offenders = []
    for path in PHASE6A_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_FUNCTION_NAMES:
            if re.search(rf"\bdef\s+{name}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"live-order function name in approval layer: {offenders}"


def test_no_broker_credential_env_reads_in_phase6a_files():
    offenders = []
    for path in PHASE6A_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"forbidden credential env reference in approval layer: {offenders}"


def test_no_outbound_http_libraries_in_phase6a_files():
    offenders = []
    for path in PHASE6A_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"outbound HTTP library in dry-run approval layer: {offenders}"


def test_no_execution_bypass_switches_in_phase6a_files():
    offenders = []
    for path in PHASE6A_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders, f"execution bypass switch in approval layer: {offenders}"


def test_approval_gate_refuses_live_trading_env(monkeypatch, tmp_path):
    from approval.approval_gate import ApprovalGate, LiveTradingForbidden
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(LiveTradingForbidden):
        ApprovalGate(log_path=str(tmp_path / "x.jsonl"))


def test_approval_gate_default_log_path_is_logs_approvals_jsonl(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from approval.approval_gate import ApprovalGate
    g = ApprovalGate(allowed_chat_ids=["1"])
    assert g.log_path == Path("logs/approvals.jsonl")


def test_approval_gate_module_does_not_pull_in_executor_or_risk():
    """Phase 6.A-1 is a pure decision recorder. It must NOT import
    executor or risk modules -- the wiring to those is a later phase
    and must remain reviewable as a separate change."""
    from approval import approval_gate as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from executor" not in src
    assert "import executor" not in src
    assert "from risk" not in src
    assert "import risk" not in src
