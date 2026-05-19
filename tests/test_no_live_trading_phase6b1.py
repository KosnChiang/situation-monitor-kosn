"""Phase 6.B-1 structural guards.

Scope: the new live/ + ai_decision/ packages and the modified
executor/executor_router.py. The repo-wide guards in
test_no_live_trading_phase6a2.py / earlier phases still apply.
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


PHASE6B1_FILES = [
    ROOT / "ai_decision" / "__init__.py",
    ROOT / "ai_decision" / "models.py",
    ROOT / "ai_decision" / "validator.py",
    ROOT / "live" / "__init__.py",
    ROOT / "live" / "models.py",
    ROOT / "live" / "audit_log.py",
    ROOT / "live" / "kill_switch.py",
    ROOT / "live" / "live_unlock_gate.py",
    ROOT / "live" / "broker_adapter_protocol.py",
    ROOT / "live" / "fake_live_adapter.py",
    ROOT / "live" / "micro_live_gate.py",
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
    "place_order", "send_order",
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
    r"^\s*import\s+requests\b", r"^\s*from\s+requests\b",
    r"^\s*import\s+httpx\b",    r"^\s*from\s+httpx\b",
    r"^\s*import\s+aiohttp\b",  r"^\s*from\s+aiohttp\b",
    r"^\s*from\s+urllib\.request\b",
]

EXECUTION_BYPASS = [
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


def test_phase6b1_files_exist():
    for p in PHASE6B1_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports():
    offenders = []
    for path in PHASE6B1_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"broker SDK import in 6.B-1 layer: {offenders}"


def test_no_live_order_function_names():
    offenders = []
    for path in PHASE6B1_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_FUNCTION_NAMES:
            if re.search(rf"\bdef\s+{name}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


def test_no_broker_credential_env_reads():
    offenders = []
    for path in PHASE6B1_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"forbidden credential env reference: {offenders}"


def test_no_outbound_http_at_module_top():
    offenders = []
    for path in PHASE6B1_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text, re.MULTILINE):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"outbound HTTP import in 6.B-1 layer: {offenders}"


def test_no_execution_bypass_switches_other_than_live_trading():
    """LIVE_TRADING=true does appear in this layer's documentation
    (live_unlock_gate explains the unlock ritual). The earlier
    test_no_live_trading_phase5/5c/6 scans already cover the rest of
    the repo. Here we only forbid the *other* bypass switches."""
    offenders = []
    for path in PHASE6B1_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders, f"non-LIVE_TRADING bypass switch: {offenders}"


def test_fake_live_adapter_never_imports_http_or_socket():
    """The fake adapter is the most likely place for an accidental
    network call to creep in. Belt-and-braces: zero socket / urllib /
    http imports."""
    src = (ROOT / "live" / "fake_live_adapter.py").read_text(encoding="utf-8")
    forbidden = [
        r"\bimport\s+socket\b",
        r"\bimport\s+urllib\b",
        r"\bimport\s+http\b",
        r"\bimport\s+requests\b",
        r"\bimport\s+httpx\b",
    ]
    for pat in forbidden:
        assert not re.search(pat, src), f"fake adapter imports {pat}"


def test_executor_router_still_refuses_live_without_token(monkeypatch, tmp_path):
    from executor.executor_router import ExecutorRouter, LiveTradingForbidden
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(log_path=str(tmp_path / "r.jsonl"))


def test_executor_router_refuses_execution_mode_live_without_token(monkeypatch, tmp_path):
    from executor.executor_router import ExecutorRouter, LiveTradingForbidden
    monkeypatch.setenv("LIVE_TRADING", "false")
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(execution_mode="live", log_path=str(tmp_path / "r.jsonl"))


def test_executor_router_refuses_token_when_not_a_real_token(tmp_path):
    """An untyped object cannot impersonate a LiveUnlockToken."""
    from executor.executor_router import ExecutorRouter, LiveTradingForbidden
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(
            log_path=str(tmp_path / "r.jsonl"),
            live_unlock_token="not-a-token",
        )


def test_executor_router_unlocks_live_with_valid_token(monkeypatch, tmp_path):
    from datetime import datetime, timezone
    from executor.executor_router import ExecutorRouter
    from live.fake_live_adapter import FakeLiveBrokerAdapter
    from live.live_unlock_gate import LiveUnlockGate

    today = f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("LIVE_READY_FLAG", today)
    monkeypatch.setenv("LIVE_TOKEN_HMAC", "abc12345")
    monkeypatch.setenv("ALLOWED_SYMBOLS", "XAUUSD")
    monkeypatch.setenv("MAX_DAILY_LOSS", "50")
    monkeypatch.setenv("MAX_POSITION_SIZE", "1")
    monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")
    monkeypatch.delenv("LIVE_BROKER_ADAPTER_PATH", raising=False)

    gate = LiveUnlockGate(kill_file_path=str(tmp_path / ".killswitch"))
    token = gate.unlock()
    adapter = FakeLiveBrokerAdapter(
        order_log_path=str(tmp_path / "o.jsonl"),
        fill_log_path=str(tmp_path / "f.jsonl"),
        rejection_log_path=str(tmp_path / "r.jsonl"),
    )
    router = ExecutorRouter(
        adapter=adapter,
        log_path=str(tmp_path / "router.jsonl"),
        live_unlock_token=token,
    )
    assert router.execution_mode == "live"
    assert router.adapter_name == "fake-live"


def test_executor_router_refuses_expired_token(monkeypatch, tmp_path):
    import time as _t
    from datetime import datetime, timezone
    from executor.executor_router import ExecutorRouter, LiveTradingForbidden
    from live.fake_live_adapter import FakeLiveBrokerAdapter
    from live.live_unlock_gate import LiveUnlockGate

    today = f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("LIVE_READY_FLAG", today)
    monkeypatch.setenv("LIVE_TOKEN_HMAC", "abc12345")
    monkeypatch.setenv("ALLOWED_SYMBOLS", "XAUUSD")
    monkeypatch.setenv("MAX_DAILY_LOSS", "50")
    monkeypatch.setenv("MAX_POSITION_SIZE", "1")
    monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")

    gate = LiveUnlockGate(
        kill_file_path=str(tmp_path / ".killswitch"),
        token_lifetime_seconds=0.05,
    )
    token = gate.unlock()
    _t.sleep(0.1)
    adapter = FakeLiveBrokerAdapter(
        order_log_path=str(tmp_path / "o.jsonl"),
        fill_log_path=str(tmp_path / "f.jsonl"),
        rejection_log_path=str(tmp_path / "r.jsonl"),
    )
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(
            adapter=adapter,
            log_path=str(tmp_path / "router.jsonl"),
            live_unlock_token=token,
        )


def test_no_class_live_broker_adapter_in_phase6b1_source():
    """The Phase 6.A-2 guard scans for ``class LiveBrokerAdapter``
    anywhere in non-test source. The Phase 6.B-1 Protocol is named
    LiveBrokerAdapterProtocol so the existing guard remains satisfied."""
    src = (ROOT / "live" / "broker_adapter_protocol.py").read_text(encoding="utf-8")
    assert "LiveBrokerAdapterProtocol" in src
    assert not re.search(r"^class\s+LiveBrokerAdapter\b", src, re.MULTILINE)


def test_live_module_records_only_mode_live(tmp_path):
    """Every dataclass in live/models.py defaults mode to 'live'."""
    from live.models import LiveFill, LiveOrder, LiveRejection
    o = LiveOrder(
        order_id="x", ts=0.0, timestamp="t", decision_id=None,
        symbol="X", side="LONG", qty=1, entry=1, stop=0, target=2,
        confidence=0.8, reason="",
    )
    f = LiveFill(
        order_id="x", ts=0.0, timestamp="t", side="LONG",
        fill_price=1, qty=1, slippage=0, status="filled",
    )
    r = LiveRejection(
        ts=0.0, timestamp="t", decision_id=None, order_id=None,
        symbol="X", side="LONG", reason="x", rejection_layer="x",
    )
    assert o.mode == "live"
    assert f.mode == "live"
    assert r.mode == "live"


def test_ai_decision_default_mode_can_be_set():
    """AIDecision.mode is a free string echoed back from the model so
    audit can show whether the AI was thinking in mock / paper / live
    terms. It's NOT a safety guarantee -- the actual execution mode
    is enforced by the gates and the router."""
    from ai_decision.models import AIDecision
    d = AIDecision(
        decision_id="x", ts=0.0, timestamp="t",
        symbol="X", side="LONG", entry=1, stop=0, target=2,
        confidence=0.8, reason="", invalidation="", data_sources=[],
        mode="mock",
    )
    assert d.mode == "mock"
