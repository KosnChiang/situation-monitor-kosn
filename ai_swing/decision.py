"""AI swing decision dataclass (Phase 7.A)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


VALID_ACTIONS = ("ENTRY", "HOLD", "EXIT", "REDUCE", "REVERSE", "NO_TRADE")
VALID_SIDES = ("LONG", "SHORT", "FLAT")


@dataclass(frozen=True)
class AISwingDecision:
    decision_id: str
    ts: float
    timestamp: str
    action: str
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str
    invalidation: str = ""
    qty: float = 1.0
    point_value: float = 10.0
    chart_context_id: Optional[str] = None
    open_order_id: Optional[str] = None
    reduce_qty_pct: Optional[float] = None
    new_stop: Optional[float] = None
    new_side: Optional[str] = None
    new_entry: Optional[float] = None
    new_stop_after_reverse: Optional[float] = None
    unrealized_points: Optional[float] = None
    unrealized_twd: Optional[float] = None
    risk_twd: Optional[float] = None
    protected_twd: Optional[float] = None
    features_triggered: list[str] = field(default_factory=list)
    mode: str = "paper"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "ai_swing_decision"
        return d
