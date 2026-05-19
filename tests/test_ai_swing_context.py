"""ChartContext builder tests (Phase 7.A)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from ai_swing.context import (  # noqa: E402
    ChartContext,
    build_chart_context_from_tv_alert,
)


def _alert(**overrides) -> dict:
    base = {
        "source": "tradingview",
        "strategy": "twtx_fibo_v4",
        "symbol": "TMFR1",
        "timeframe": "5",
        "bar_time": "2026-05-20T14:55:00Z",
        "open":  23015.0,
        "high":  23022.0,
        "low":   22995.0,
        "close": 23018.0,
        "prev_ohlc": {"open": 23000.0, "high": 23080.0, "low": 22970.0, "close": 23055.0},
        "fibo": {
            "base": 23026.25, "range": 110.0,
            "nearest_ratio": 0.382, "nearest_price": 23068.27,
            "touch_support": True, "touch_resistance": False,
            "zone": "support",
        },
        "rsi": {
            "value": 28.5, "ma": 42.1, "state": "oversold",
            "bull_div": True, "bear_div": False,
        },
        "pivot": {"last_high": 23110.0, "last_low": 22960.0},
        "signal": {
            "buy": False, "sell": False,
            "combo_buy": True, "combo_sell": False,
            "div_buy": True, "div_sell": False,
            "touch_fibo": True,
        },
    }
    base.update(overrides)
    return base


def test_builds_context_from_full_alert():
    ctx = build_chart_context_from_tv_alert(
        _alert(), context_id="ctx-1", ts=1.0, timestamp="t",
    )
    assert isinstance(ctx, ChartContext)
    assert ctx.symbol == "TMFR1"
    assert ctx.close == 23018.0
    assert ctx.prev_ohlc.high == 23080.0
    assert ctx.fibo.touch_support is True
    assert ctx.fibo.zone == "support"
    assert ctx.rsi.state == "oversold"
    assert ctx.rsi.bull_div is True
    assert ctx.pivot.last_low == 22960.0
    assert ctx.signal.combo_buy is True
    assert ctx.signal.combo_sell is False


def test_to_log_dict_contains_event_type():
    ctx = build_chart_context_from_tv_alert(
        _alert(), context_id="ctx-1", ts=1.0, timestamp="t",
    )
    d = ctx.to_log_dict()
    assert d["event_type"] == "chart_context"
    assert d["context_id"] == "ctx-1"


def test_handles_string_booleans_for_signals():
    alert = _alert()
    alert["signal"]["combo_buy"] = "true"
    alert["signal"]["combo_sell"] = "false"
    ctx = build_chart_context_from_tv_alert(
        alert, context_id="ctx-2", ts=1.0, timestamp="t",
    )
    assert ctx.signal.combo_buy is True
    assert ctx.signal.combo_sell is False


def test_default_strategy_and_source_when_missing():
    alert = _alert()
    alert.pop("source")
    alert.pop("strategy")
    ctx = build_chart_context_from_tv_alert(
        alert, context_id="ctx-3", ts=1.0, timestamp="t",
    )
    assert ctx.source == "tradingview"
    assert ctx.strategy == "twtx_fibo_v4"


def test_missing_required_top_field_raises():
    alert = _alert()
    alert.pop("close")
    with pytest.raises((KeyError, TypeError)):
        build_chart_context_from_tv_alert(
            alert, context_id="ctx-4", ts=1.0, timestamp="t",
        )
