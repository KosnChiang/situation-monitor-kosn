"""Unit tests for live.micro_live_gate.MicroLiveGate (Phase 6.B-1)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from ai_decision.models import AIDecision  # noqa: E402
from live.micro_live_gate import MicroLiveGate, MicroLiveResult  # noqa: E402


def _decision(**overrides) -> AIDecision:
    base = dict(
        decision_id="ai-1",
        ts=1779000000.0,
        timestamp="2026-05-19T00:00:00+00:00",
        symbol="XAUUSD",
        side="LONG",
        entry=23010.0,
        stop=22995.0,
        target=23040.0,
        confidence=0.85,
        reason="test",
        invalidation="",
        data_sources=[],
        mode="live",
    )
    base.update(overrides)
    return AIDecision(**base)


def _gate(tmp_path: Path, **kw) -> MicroLiveGate:
    return MicroLiveGate(
        allowed_symbols=kw.pop("allowed_symbols", ["XAUUSD", "EURUSD"]),
        max_position_size=kw.pop("max_position_size", 1.0),
        max_daily_trades=kw.pop("max_daily_trades", 3),
        max_loss_per_trade=kw.pop("max_loss_per_trade", 30.0),
        max_daily_loss=kw.pop("max_daily_loss", 50.0),
        min_live_confidence=kw.pop("min_live_confidence", 0.7),
        rejection_log_path=str(tmp_path / "rejections.jsonl"),
        **kw,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_approves_normal_order(tmp_path):
    g = _gate(tmp_path)
    r = g.check(decision=_decision(), qty=1.0, daily_trade_count=0, daily_loss=0.0)
    assert r.approved is True
    assert r.reasons == []


def test_rejects_unknown_symbol(tmp_path):
    g = _gate(tmp_path)
    r = g.check(decision=_decision(symbol="BTCUSD"), qty=1.0,
                daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("symbol_not_allowed" in s for s in r.reasons)
    rejs = _read_jsonl(g.rejection_log_path)
    assert rejs and rejs[-1]["rejection_layer"] == "micro_live_gate"


def test_rejects_qty_exceeds_max(tmp_path):
    g = _gate(tmp_path, max_position_size=1.0)
    r = g.check(decision=_decision(), qty=2.0, daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("qty_exceeds_max" in s for s in r.reasons)


def test_rejects_daily_trades_at_cap(tmp_path):
    g = _gate(tmp_path, max_daily_trades=3)
    r = g.check(decision=_decision(), qty=1.0, daily_trade_count=3, daily_loss=0.0)
    assert r.approved is False
    assert any("daily_trades_at_cap" in s for s in r.reasons)


def test_rejects_per_trade_risk_exceeds(tmp_path):
    g = _gate(tmp_path, max_loss_per_trade=10.0)
    # entry-stop = 15, qty=1, risk=15 > 10
    r = g.check(
        decision=_decision(entry=23010.0, stop=22995.0),
        qty=1.0, daily_trade_count=0, daily_loss=0.0,
    )
    assert r.approved is False
    assert any("per_trade_risk_exceeds_max" in s for s in r.reasons)


def test_rejects_daily_loss_cap_would_exceed(tmp_path):
    g = _gate(tmp_path, max_daily_loss=20.0)
    # already lost 10, this trade risks 15, total 25 > 20
    r = g.check(
        decision=_decision(entry=23010.0, stop=22995.0),
        qty=1.0, daily_trade_count=0, daily_loss=10.0,
    )
    assert r.approved is False
    assert any("daily_loss_cap_would_exceed" in s for s in r.reasons)


def test_rejects_flat_side(tmp_path):
    g = _gate(tmp_path)
    r = g.check(decision=_decision(side="FLAT"), qty=1.0,
                daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("side_not_tradable" in s for s in r.reasons)


def test_rejects_watch_side(tmp_path):
    g = _gate(tmp_path)
    r = g.check(decision=_decision(side="WATCH"), qty=1.0,
                daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("side_not_tradable" in s for s in r.reasons)


def test_rejects_missing_stop(tmp_path):
    g = _gate(tmp_path)
    r = g.check(decision=_decision(stop=23010.0),  # stop == entry
                qty=1.0, daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("missing_or_zero_stop_distance" in s for s in r.reasons)


def test_rejects_missing_target(tmp_path):
    g = _gate(tmp_path)
    r = g.check(decision=_decision(target=23010.0),  # target == entry
                qty=1.0, daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("missing_or_zero_target_distance" in s for s in r.reasons)


def test_rejects_confidence_below_min(tmp_path):
    g = _gate(tmp_path, min_live_confidence=0.8)
    r = g.check(decision=_decision(confidence=0.5),
                qty=1.0, daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("confidence_below_min" in s for s in r.reasons)


def test_rejects_stop_distance_unreasonable(tmp_path):
    g = _gate(tmp_path, max_loss_per_trade=10000.0, max_daily_loss=10000.0)
    # stop is 60% away from entry, exceeds 50% sanity check
    r = g.check(
        decision=_decision(entry=100.0, stop=40.0, target=160.0),
        qty=1.0, daily_trade_count=0, daily_loss=0.0,
    )
    assert r.approved is False
    assert any("stop_distance_unreasonable" in s for s in r.reasons)


def test_rejects_with_empty_allowed_symbols(tmp_path):
    g = _gate(tmp_path, allowed_symbols=[])
    r = g.check(decision=_decision(), qty=1.0, daily_trade_count=0, daily_loss=0.0)
    assert r.approved is False
    assert any("allowed_symbols_empty" in s for s in r.reasons)


def test_approved_does_not_write_rejection_log(tmp_path):
    g = _gate(tmp_path)
    g.check(decision=_decision(), qty=1.0, daily_trade_count=0, daily_loss=0.0)
    assert not g.rejection_log_path.exists() \
        or g.rejection_log_path.read_text("utf-8").strip() == ""
