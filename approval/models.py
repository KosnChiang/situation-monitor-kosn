"""Dataclasses for the Phase 6.A-1 human-in-the-loop approval layer.

Mock-only. No broker SDK, no broker credential, no network. The fields
on these records are deliberately a strict superset of what a future
Telegram bot callback would need so the wire format does not need to
change when the bot is wired in.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional


@dataclass(frozen=True)
class ApprovalRequest:
    """One request for human approval of a candidate trade."""

    request_id: str
    ts: float
    timestamp: str
    signal_id: Optional[str]
    symbol: str
    side: str
    entry: float
    stop: float
    target: float
    confidence: float
    qty: float
    reason: str
    timeout_seconds: float
    mode: str = "mock"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "approval_request"
        return d


@dataclass(frozen=True)
class ApprovalDecision:
    """Final disposition of an ApprovalRequest.

    ``reason`` is one of:
        approved, manual_reject, approval_timeout, unauthorized_operator.
    """

    request_id: str
    approved: bool
    reason: str
    operator_chat_id: Optional[str]
    decided_ts: float
    decided_timestamp: str
    elapsed_ms: float
    mode: str = "mock"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "approval_decision"
        return d
