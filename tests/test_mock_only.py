"""Guardrail tests that confirm no live-trading code path exists.

These tests are stdlib-only on purpose so they pass on a fresh checkout
before `pip install -r requirements.txt` has even been run.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Force mock mode for the duration of the test process.
os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"
os.environ["TRADES_LOG"] = str(ROOT / "logs" / "trades.test.jsonl")


PROJECT_DIRS = ["app", "capture", "vision", "strategy", "risk", "executor", "notify", "quote"]

FORBIDDEN_IMPORTS = [
    # Real-money broker / exchange SDKs.
    r"\bimport\s+ib_insync\b",
    r"\bfrom\s+ib_insync\b",
    r"\bimport\s+ibapi\b",
    r"\bfrom\s+ibapi\b",
    r"\bimport\s+alpaca\b",
    r"\bfrom\s+alpaca\b",
    r"\bimport\s+ccxt\b",
    r"\bfrom\s+ccxt\b",
    r"\bimport\s+binance\b",
    r"\bfrom\s+binance\b",
    r"\bimport\s+oandapyV20\b",
    r"\bfrom\s+oandapyV20\b",
    r"\bimport\s+MetaTrader5\b",
    r"\bfrom\s+MetaTrader5\b",
    r"\bimport\s+shioaji\b",
    r"\bfrom\s+shioaji\b",
]

FORBIDDEN_NAMES = [
    "place_order",
    "submit_order",
    "send_order",
    "live_order",
    "real_order",
    "place_live_order",
    "execute_live",
]


def _iter_source_files():
    for d in PROJECT_DIRS:
        for p in (ROOT / d).rglob("*.py"):
            yield p


def test_no_forbidden_broker_imports():
    offenders = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORTS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"Forbidden broker import found: {offenders}"


def test_no_live_order_function_names():
    offenders = []
    for path in _iter_source_files():
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_NAMES:
            if re.search(rf"\bdef\s+{name}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"Live-order function defined: {offenders}"


def test_risk_gate_refuses_live_mode(monkeypatch):
    from risk.risk_gate import RiskGate, LiveTradingForbidden

    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    with pytest.raises(LiveTradingForbidden):
        RiskGate()

    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    with pytest.raises(LiveTradingForbidden):
        RiskGate()


def test_mock_executor_refuses_when_env_flipped(monkeypatch, tmp_path):
    from executor.mock_executor import MockExecutor
    from strategy.fibo_mob_v2 import Signal

    ex = MockExecutor(log_path=str(tmp_path / "trades.jsonl"))
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "test")

    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(RuntimeError):
        ex.submit(sig)

    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    with pytest.raises(RuntimeError):
        ex.submit(sig)


def test_mock_executor_writes_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    from executor.mock_executor import MockExecutor
    from strategy.fibo_mob_v2 import Signal

    log = tmp_path / "trades.jsonl"
    ex = MockExecutor(log_path=str(log))
    fill = ex.submit(Signal("LONG", 100.0, 99.0, 110.0, 0.8, "unit"))
    assert fill.side == "LONG"
    assert fill.mode == "mock"

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["mode"] == "mock"
    assert rec["side"] == "LONG"


def test_risk_gate_filters_low_confidence_and_flat(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    from risk.risk_gate import RiskGate
    from strategy.fibo_mob_v2 import Signal

    gate = RiskGate(min_confidence=0.6)
    assert not gate.check(Signal("FLAT", 1, 1, 1, 0.99, "")).approved
    assert not gate.check(Signal("LONG", 1, 1, 1, 0.5, "")).approved
    assert gate.check(Signal("LONG", 1, 1, 1, 0.9, "")).approved


def test_strategy_emits_flat_without_detection():
    from strategy.fibo_mob_v2 import FiboMobV2
    from vision.fibo_detector import FiboDetection

    s = FiboMobV2()
    out = s.evaluate(FiboDetection(), last_price=100.0)
    assert out.side == "FLAT"
