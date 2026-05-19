"""ChartContext dataclass + builder from TradingView alert JSON (Phase 7.A)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class PrevOHLC:
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class FiboContext:
    base: float
    range: float
    nearest_ratio: float
    nearest_price: float
    touch_support: bool
    touch_resistance: bool
    zone: str


@dataclass(frozen=True)
class RsiContext:
    value: float
    ma: float
    state: str
    bull_div: bool
    bear_div: bool


@dataclass(frozen=True)
class PivotContext:
    last_high: float
    last_low: float


@dataclass(frozen=True)
class SignalContext:
    buy: bool
    sell: bool
    combo_buy: bool
    combo_sell: bool
    div_buy: bool
    div_sell: bool
    touch_fibo: bool


@dataclass(frozen=True)
class ChartContext:
    context_id: str
    ts: float
    timestamp: str
    source: str
    strategy: str
    symbol: str
    timeframe: str
    bar_time: str
    open: float
    high: float
    low: float
    close: float
    prev_ohlc: PrevOHLC
    fibo: FiboContext
    rsi: RsiContext
    pivot: PivotContext
    signal: SignalContext

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "chart_context"
        return d


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return False


def build_chart_context_from_tv_alert(
    alert: dict, *, context_id: str, ts: float, timestamp: str,
) -> ChartContext:
    """Parse a TradingView alert dict into a ChartContext."""
    prev = alert.get("prev_ohlc", {})
    fibo = alert.get("fibo", {})
    rsi = alert.get("rsi", {})
    pivot = alert.get("pivot", {})
    signal = alert.get("signal", {})

    return ChartContext(
        context_id=context_id,
        ts=float(ts),
        timestamp=str(timestamp),
        source=str(alert.get("source", "tradingview")),
        strategy=str(alert.get("strategy", "twtx_fibo_v4")),
        symbol=str(alert["symbol"]),
        timeframe=str(alert.get("timeframe", "")),
        bar_time=str(alert.get("bar_time", "")),
        open=float(alert["open"]),
        high=float(alert["high"]),
        low=float(alert["low"]),
        close=float(alert["close"]),
        prev_ohlc=PrevOHLC(
            open=float(prev.get("open", 0.0)),
            high=float(prev.get("high", 0.0)),
            low=float(prev.get("low", 0.0)),
            close=float(prev.get("close", 0.0)),
        ),
        fibo=FiboContext(
            base=float(fibo.get("base", 0.0)),
            range=float(fibo.get("range", 0.0)),
            nearest_ratio=float(fibo.get("nearest_ratio", 0.0)),
            nearest_price=float(fibo.get("nearest_price", 0.0)),
            touch_support=_to_bool(fibo.get("touch_support", False)),
            touch_resistance=_to_bool(fibo.get("touch_resistance", False)),
            zone=str(fibo.get("zone", "neutral")),
        ),
        rsi=RsiContext(
            value=float(rsi.get("value", 0.0)),
            ma=float(rsi.get("ma", 0.0)),
            state=str(rsi.get("state", "neutral")),
            bull_div=_to_bool(rsi.get("bull_div", False)),
            bear_div=_to_bool(rsi.get("bear_div", False)),
        ),
        pivot=PivotContext(
            last_high=float(pivot.get("last_high", 0.0)),
            last_low=float(pivot.get("last_low", 0.0)),
        ),
        signal=SignalContext(
            buy=_to_bool(signal.get("buy", False)),
            sell=_to_bool(signal.get("sell", False)),
            combo_buy=_to_bool(signal.get("combo_buy", False)),
            combo_sell=_to_bool(signal.get("combo_sell", False)),
            div_buy=_to_bool(signal.get("div_buy", False)),
            div_sell=_to_bool(signal.get("div_sell", False)),
            touch_fibo=_to_bool(signal.get("touch_fibo", False)),
        ),
    )
