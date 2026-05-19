"""Paper executor dataclasses (Phase 6.A-3 minimal v1).

Mock-only. No slippage, no expiry, no account snapshot. The minimal
shape needed to record one paper_open / paper_close lifecycle per
position. Future phases may extend, but the existing fields are
stable wire-format from here onwards.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PaperFill:
    """Returned by PaperExecutor.submit(). Mirrors the shape ExecutorRouter
    expects from any BrokerAdapter (``ts`` attribute accessed via getattr)."""

    ts: float
    timestamp: str
    position_id: str
    side: str
    entry: float
    stop: float
    target: float
    qty: float
    confidence: float
    reason: str
    mode: str = "paper"


@dataclass(frozen=True)
class PaperPosition:
    """Position book entry. Open positions have ``status == "open"``;
    close-side fields are None until tick() flips them."""

    position_id: str
    ts_opened: float
    timestamp_opened: str
    side: str
    qty: float
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str
    status: str = "open"
    ts_closed: Optional[float] = None
    timestamp_closed: Optional[str] = None
    exit_price: Optional[float] = None
    close_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    hold_seconds: Optional[float] = None
