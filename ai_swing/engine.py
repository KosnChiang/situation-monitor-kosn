"""Deterministic rule engine for AI swing decisions (Phase 7.A).

No LLM in v1. Pure rule-based decision tree over the ChartContext,
position state, and cooldown state. The LLM integration is Phase 7.B+.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from ai_swing.context import ChartContext
from ai_swing.decision import AISwingDecision
from ai_swing.tmf_pnl import (
    POINT_VALUE_TWD,
    protected_profit_twd,
    risk_twd,
    unrealized_points,
    unrealized_twd,
)


@dataclass(frozen=True)
class PositionState:
    side: str
    entry: float
    qty: float
    open_order_id: str
    initial_stop: float
    current_stop: Optional[float] = None
    bars_held: int = 0


@dataclass(frozen=True)
class CooldownState:
    last_action: str
    last_ts: float
    cooldown_seconds: float = 300.0


def _now() -> tuple[float, str]:
    return time.time(), datetime.now(timezone.utc).isoformat()


def _make_decision(
    *,
    action: str,
    ctx: ChartContext,
    side: str = "FLAT",
    entry: float = 0.0,
    stop: float = 0.0,
    target: float = 0.0,
    confidence: float = 0.6,
    reason: str = "",
    invalidation: str = "",
    features_triggered: Optional[list[str]] = None,
    mode: str = "paper",
    **kwargs,
) -> AISwingDecision:
    ts, iso = _now()
    return AISwingDecision(
        decision_id=str(uuid.uuid4()),
        ts=ts,
        timestamp=iso,
        action=action,
        symbol=ctx.symbol,
        side=side,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        confidence=float(confidence),
        reason=reason,
        invalidation=invalidation,
        chart_context_id=ctx.context_id,
        features_triggered=list(features_triggered or []),
        mode=mode,
        **kwargs,
    )


def decide(
    *,
    ctx: ChartContext,
    position: Optional[PositionState] = None,
    cooldown: Optional[CooldownState] = None,
    mode: str = "paper",
) -> AISwingDecision:
    """Apply deterministic rules to produce a swing decision."""
    # ---- Cooldown ----
    if cooldown is not None and cooldown.last_action in ("ENTRY", "REVERSE"):
        elapsed = time.time() - cooldown.last_ts
        if elapsed < cooldown.cooldown_seconds:
            remaining = cooldown.cooldown_seconds - elapsed
            return _make_decision(
                action="NO_TRADE", ctx=ctx, side="FLAT",
                reason=f"cooldown active: {remaining:.0f}s remaining",
                features_triggered=["cooldown"], mode=mode,
            )

    # ---- No position: ENTRY logic ----
    if position is None:
        # ENTRY LONG: combo BUY + support touch + RSI oversold
        if (ctx.signal.combo_buy
            and ctx.fibo.touch_support
            and ctx.rsi.state in ("oversold", "extreme_oversold")):
            entry_px = ctx.close
            stop_px = ctx.pivot.last_low
            return _make_decision(
                action="ENTRY", ctx=ctx, side="LONG",
                entry=entry_px, stop=stop_px, target=0.0,
                confidence=0.85,
                reason="combo_buy + fibo support touch + RSI oversold",
                invalidation=f"close below pivot low {stop_px}",
                features_triggered=["combo_buy", "touch_support", f"rsi_{ctx.rsi.state}"],
                risk_twd=risk_twd(entry_px, stop_px),
                mode=mode,
            )
        # ENTRY SHORT: combo SELL + resistance touch + RSI overbought
        if (ctx.signal.combo_sell
            and ctx.fibo.touch_resistance
            and ctx.rsi.state in ("overbought", "extreme_overbought")):
            entry_px = ctx.close
            stop_px = ctx.pivot.last_high
            return _make_decision(
                action="ENTRY", ctx=ctx, side="SHORT",
                entry=entry_px, stop=stop_px, target=0.0,
                confidence=0.85,
                reason="combo_sell + fibo resistance touch + RSI overbought",
                invalidation=f"close above pivot high {stop_px}",
                features_triggered=["combo_sell", "touch_resistance", f"rsi_{ctx.rsi.state}"],
                risk_twd=risk_twd(entry_px, stop_px),
                mode=mode,
            )
        return _make_decision(
            action="NO_TRADE", ctx=ctx, side="FLAT",
            reason="no entry conditions met",
            features_triggered=[], mode=mode,
        )

    # ---- Have position ----
    unreal = unrealized_twd(position.side, position.entry, ctx.close, position.qty)
    pts = unrealized_points(position.side, position.entry, ctx.close)
    stop_now = position.current_stop if position.current_stop is not None else position.initial_stop

    if position.side == "LONG":
        # REVERSE: combo_sell + break pivot low
        if ctx.signal.combo_sell and ctx.close < ctx.pivot.last_low:
            return _make_decision(
                action="REVERSE", ctx=ctx, side="LONG",
                entry=position.entry, stop=stop_now,
                confidence=0.9,
                reason="combo_sell + close below pivot low; strong reverse",
                features_triggered=["combo_sell", "break_pivot_low"],
                open_order_id=position.open_order_id,
                new_side="SHORT",
                new_entry=ctx.close,
                new_stop_after_reverse=ctx.pivot.last_high,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        # EXIT triggers
        if ctx.rsi.bear_div:
            return _make_decision(
                action="EXIT", ctx=ctx, side="LONG",
                entry=ctx.close, stop=ctx.close,
                confidence=0.8, reason="bear divergence detected",
                features_triggered=["rsi_bear_div"],
                open_order_id=position.open_order_id,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        if ctx.signal.combo_sell:
            return _make_decision(
                action="EXIT", ctx=ctx, side="LONG",
                entry=ctx.close, stop=ctx.close,
                confidence=0.75, reason="combo_sell signal while LONG",
                features_triggered=["combo_sell"],
                open_order_id=position.open_order_id,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        if ctx.close < ctx.pivot.last_low:
            return _make_decision(
                action="EXIT", ctx=ctx, side="LONG",
                entry=ctx.close, stop=ctx.close,
                confidence=0.75,
                reason=f"close {ctx.close} below pivot low {ctx.pivot.last_low}",
                features_triggered=["break_pivot_low"],
                open_order_id=position.open_order_id,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        # REDUCE: overbought without pivot break
        if ctx.rsi.state in ("overbought", "extreme_overbought"):
            new_stop_val = max(position.entry, stop_now)
            return _make_decision(
                action="REDUCE", ctx=ctx, side="LONG",
                entry=position.entry, stop=stop_now,
                confidence=0.65,
                reason=f"RSI {ctx.rsi.state} while LONG; reduce 50% and trail",
                features_triggered=[f"rsi_{ctx.rsi.state}"],
                open_order_id=position.open_order_id,
                reduce_qty_pct=50.0, new_stop=new_stop_val,
                unrealized_points=pts, unrealized_twd=unreal,
                protected_twd=protected_profit_twd(
                    position.side, position.entry, new_stop_val, position.qty
                ),
                mode=mode,
            )
        return _make_decision(
            action="HOLD", ctx=ctx, side="LONG",
            entry=position.entry, stop=stop_now,
            confidence=0.7, reason="LONG structure intact",
            features_triggered=[],
            open_order_id=position.open_order_id,
            unrealized_points=pts, unrealized_twd=unreal,
            protected_twd=protected_profit_twd(
                position.side, position.entry, stop_now, position.qty
            ),
            mode=mode,
        )

    if position.side == "SHORT":
        if ctx.signal.combo_buy and ctx.close > ctx.pivot.last_high:
            return _make_decision(
                action="REVERSE", ctx=ctx, side="SHORT",
                entry=position.entry, stop=stop_now,
                confidence=0.9,
                reason="combo_buy + close above pivot high; strong reverse",
                features_triggered=["combo_buy", "break_pivot_high"],
                open_order_id=position.open_order_id,
                new_side="LONG",
                new_entry=ctx.close,
                new_stop_after_reverse=ctx.pivot.last_low,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        if ctx.rsi.bull_div:
            return _make_decision(
                action="EXIT", ctx=ctx, side="SHORT",
                entry=ctx.close, stop=ctx.close,
                confidence=0.8, reason="bull divergence detected",
                features_triggered=["rsi_bull_div"],
                open_order_id=position.open_order_id,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        if ctx.signal.combo_buy:
            return _make_decision(
                action="EXIT", ctx=ctx, side="SHORT",
                entry=ctx.close, stop=ctx.close,
                confidence=0.75, reason="combo_buy signal while SHORT",
                features_triggered=["combo_buy"],
                open_order_id=position.open_order_id,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        if ctx.close > ctx.pivot.last_high:
            return _make_decision(
                action="EXIT", ctx=ctx, side="SHORT",
                entry=ctx.close, stop=ctx.close,
                confidence=0.75,
                reason=f"close {ctx.close} above pivot high {ctx.pivot.last_high}",
                features_triggered=["break_pivot_high"],
                open_order_id=position.open_order_id,
                unrealized_points=pts, unrealized_twd=unreal,
                mode=mode,
            )
        if ctx.rsi.state in ("oversold", "extreme_oversold"):
            new_stop_val = min(position.entry, stop_now)
            return _make_decision(
                action="REDUCE", ctx=ctx, side="SHORT",
                entry=position.entry, stop=stop_now,
                confidence=0.65,
                reason=f"RSI {ctx.rsi.state} while SHORT; reduce 50% and trail",
                features_triggered=[f"rsi_{ctx.rsi.state}"],
                open_order_id=position.open_order_id,
                reduce_qty_pct=50.0, new_stop=new_stop_val,
                unrealized_points=pts, unrealized_twd=unreal,
                protected_twd=protected_profit_twd(
                    position.side, position.entry, new_stop_val, position.qty
                ),
                mode=mode,
            )
        return _make_decision(
            action="HOLD", ctx=ctx, side="SHORT",
            entry=position.entry, stop=stop_now,
            confidence=0.7, reason="SHORT structure intact",
            features_triggered=[],
            open_order_id=position.open_order_id,
            unrealized_points=pts, unrealized_twd=unreal,
            protected_twd=protected_profit_twd(
                position.side, position.entry, stop_now, position.qty
            ),
            mode=mode,
        )

    return _make_decision(
        action="NO_TRADE", ctx=ctx, side="FLAT",
        reason=f"unknown position side: {position.side}",
        mode=mode,
    )
