"""Deterministic engine tests (Phase 7.A)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from ai_swing.context import build_chart_context_from_tv_alert  # noqa: E402
from ai_swing.engine import CooldownState, PositionState, decide  # noqa: E402


def _alert(**overrides) -> dict:
    base = {
        "source": "tradingview", "strategy": "twtx_fibo_v4",
        "symbol": "TMFR1", "timeframe": "5", "bar_time": "t",
        "open":  23015.0, "high": 23022.0, "low": 22995.0, "close": 23018.0,
        "prev_ohlc": {"open": 23000.0, "high": 23080.0, "low": 22970.0, "close": 23055.0},
        "fibo": {
            "base": 23026.25, "range": 110.0,
            "nearest_ratio": 0.382, "nearest_price": 23068.27,
            "touch_support": False, "touch_resistance": False,
            "zone": "neutral",
        },
        "rsi": {
            "value": 50.0, "ma": 50.0, "state": "neutral",
            "bull_div": False, "bear_div": False,
        },
        "pivot": {"last_high": 23110.0, "last_low": 22960.0},
        "signal": {
            "buy": False, "sell": False,
            "combo_buy": False, "combo_sell": False,
            "div_buy": False, "div_sell": False,
            "touch_fibo": False,
        },
    }
    # deep-merge overrides
    for k, v in overrides.items():
        if isinstance(v, dict) and k in base and isinstance(base[k], dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def _ctx(**alert_overrides):
    return build_chart_context_from_tv_alert(
        _alert(**alert_overrides), context_id="ctx", ts=1.0, timestamp="t",
    )


# ---------- ENTRY ----------

def test_combo_buy_support_oversold_yields_entry_long():
    ctx = _ctx(
        fibo={"touch_support": True},
        rsi={"state": "oversold"},
        signal={"combo_buy": True},
    )
    d = decide(ctx=ctx, position=None)
    assert d.action == "ENTRY"
    assert d.side == "LONG"
    assert d.entry == ctx.close
    assert d.stop == ctx.pivot.last_low


def test_combo_sell_resistance_overbought_yields_entry_short():
    ctx = _ctx(
        fibo={"touch_resistance": True},
        rsi={"state": "overbought"},
        signal={"combo_sell": True},
    )
    d = decide(ctx=ctx, position=None)
    assert d.action == "ENTRY"
    assert d.side == "SHORT"


def test_no_signal_yields_no_trade():
    ctx = _ctx()
    d = decide(ctx=ctx, position=None)
    assert d.action == "NO_TRADE"


def test_extreme_oversold_also_triggers_entry():
    ctx = _ctx(
        fibo={"touch_support": True},
        rsi={"state": "extreme_oversold"},
        signal={"combo_buy": True},
    )
    d = decide(ctx=ctx, position=None)
    assert d.action == "ENTRY" and d.side == "LONG"


# ---------- LONG position transitions ----------

def _long_position():
    return PositionState(
        side="LONG", entry=23010.0, qty=1.0,
        open_order_id="ord-1", initial_stop=22995.0,
    )


def test_long_bear_div_yields_exit():
    ctx = _ctx(rsi={"bear_div": True})
    d = decide(ctx=ctx, position=_long_position())
    assert d.action == "EXIT"
    assert "bear" in d.reason.lower()


def test_long_combo_sell_yields_exit():
    ctx = _ctx(signal={"combo_sell": True}, close=23000.0)
    d = decide(ctx=ctx, position=_long_position())
    assert d.action == "EXIT"


def test_long_break_pivot_low_yields_exit():
    ctx = _ctx(close=22950.0)  # below pivot.last_low=22960
    d = decide(ctx=ctx, position=_long_position())
    assert d.action == "EXIT"


def test_long_combo_sell_and_break_pivot_low_yields_reverse():
    ctx = _ctx(signal={"combo_sell": True}, close=22950.0)
    d = decide(ctx=ctx, position=_long_position())
    assert d.action == "REVERSE"
    assert d.new_side == "SHORT"


def test_long_overbought_no_break_yields_reduce():
    # overbought + still above pivot low + no signals -> reduce
    ctx = _ctx(rsi={"state": "overbought"})
    d = decide(ctx=ctx, position=_long_position())
    assert d.action == "REDUCE"
    assert d.reduce_qty_pct == 50.0


def test_long_clean_structure_yields_hold():
    ctx = _ctx()
    d = decide(ctx=ctx, position=_long_position())
    assert d.action == "HOLD"


# ---------- SHORT position transitions ----------

def _short_position():
    return PositionState(
        side="SHORT", entry=23080.0, qty=1.0,
        open_order_id="ord-2", initial_stop=23110.0,
    )


def test_short_bull_div_yields_exit():
    ctx = _ctx(rsi={"bull_div": True})
    d = decide(ctx=ctx, position=_short_position())
    assert d.action == "EXIT"


def test_short_combo_buy_yields_exit():
    ctx = _ctx(signal={"combo_buy": True})
    d = decide(ctx=ctx, position=_short_position())
    assert d.action == "EXIT"


def test_short_break_pivot_high_yields_exit():
    ctx = _ctx(close=23150.0)  # above pivot.last_high=23110
    d = decide(ctx=ctx, position=_short_position())
    assert d.action == "EXIT"


def test_short_combo_buy_and_break_pivot_high_yields_reverse():
    ctx = _ctx(signal={"combo_buy": True}, close=23150.0)
    d = decide(ctx=ctx, position=_short_position())
    assert d.action == "REVERSE"
    assert d.new_side == "LONG"


def test_short_oversold_no_break_yields_reduce():
    ctx = _ctx(rsi={"state": "oversold"})
    d = decide(ctx=ctx, position=_short_position())
    assert d.action == "REDUCE"


def test_short_clean_yields_hold():
    ctx = _ctx()
    d = decide(ctx=ctx, position=_short_position())
    assert d.action == "HOLD"


# ---------- Cooldown ----------

def test_cooldown_blocks_entry_during_window():
    ctx = _ctx(
        fibo={"touch_support": True},
        rsi={"state": "oversold"},
        signal={"combo_buy": True},
    )
    cd = CooldownState(
        last_action="ENTRY",
        last_ts=time.time(),  # just now
        cooldown_seconds=300.0,
    )
    d = decide(ctx=ctx, position=None, cooldown=cd)
    assert d.action == "NO_TRADE"
    assert "cooldown" in d.reason.lower()


def test_cooldown_expired_allows_entry():
    ctx = _ctx(
        fibo={"touch_support": True},
        rsi={"state": "oversold"},
        signal={"combo_buy": True},
    )
    cd = CooldownState(
        last_action="ENTRY",
        last_ts=time.time() - 600,  # 10 min ago
        cooldown_seconds=300.0,
    )
    d = decide(ctx=ctx, position=None, cooldown=cd)
    assert d.action == "ENTRY"


def test_cooldown_after_hold_does_not_block():
    """Only ENTRY/REVERSE trigger cooldown; HOLD does not."""
    ctx = _ctx(
        fibo={"touch_support": True},
        rsi={"state": "oversold"},
        signal={"combo_buy": True},
    )
    cd = CooldownState(
        last_action="HOLD",
        last_ts=time.time(),
        cooldown_seconds=300.0,
    )
    d = decide(ctx=ctx, position=None, cooldown=cd)
    assert d.action == "ENTRY"
