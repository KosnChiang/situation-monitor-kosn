"""AI Decision dataclasses (Phase 6.B-1).

The wire shape an LLM (Hermes, Claude, or any other) is expected to
emit. ``side`` accepts ``WATCH`` in addition to LONG/SHORT/FLAT so
the model can communicate "I see a setup forming but won't commit
yet" without forcing FLAT.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


VALID_SIDES = ("LONG", "SHORT", "FLAT", "WATCH")


@dataclass(frozen=True)
class AIDecision:
    decision_id: str
    ts: float
    timestamp: str
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str
    invalidation: str = ""
    data_sources: list[str] = field(default_factory=list)
    mode: str = "mock"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "ai_decision"
        return d
