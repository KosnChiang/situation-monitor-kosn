"""Live order / fill / rejection dataclasses (Phase 6.B-1).

Mock-only construction:
  * mode is hard-coded to "live" so audit rows can be filtered cleanly.
  * No fields here imply any specific broker SDK.
  * dataclasses are frozen; immutable once written.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class LiveOrder:
    order_id: str
    ts: float
    timestamp: str
    decision_id: Optional[str]
    symbol: str
    side: str
    qty: float
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str
    mode: str = "live"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "live_order"
        return d


@dataclass(frozen=True)
class LiveFill:
    order_id: str
    ts: float
    timestamp: str
    side: str
    fill_price: float
    qty: float
    slippage: float
    status: str
    mode: str = "live"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "live_fill"
        return d


@dataclass(frozen=True)
class LiveRejection:
    ts: float
    timestamp: str
    decision_id: Optional[str]
    order_id: Optional[str]
    symbol: str
    side: str
    reason: str
    rejection_layer: str
    mode: str = "live"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "live_rejection"
        return d
