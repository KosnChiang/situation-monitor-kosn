"""FakeLiveBrokerAdapter (Phase 6.B-1).

Mock-only. Implements ``LiveBrokerAdapterProtocol`` so the full live
order path can be exercised end-to-end without a real broker
binding. Simulates immediate fill at the order's entry price with
zero slippage and writes both the order and the fill to the live
audit logs.

Strictly:
  * No outbound HTTP, ever. The structural guard in
    tests/test_no_live_trading_phase6b1.py scans this file for
    requests / httpx / aiohttp / urllib.request.
  * No broker SDK import.
  * No broker credential env read.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from live.audit_log import (
    default_fill_log_path,
    default_order_log_path,
    default_rejection_log_path,
    write_live_fill,
    write_live_order,
)
from live.models import LiveFill, LiveOrder


class FakeLiveBrokerAdapter:
    """An in-repo, zero-network adapter that simulates a live broker."""

    name: str = "fake-live"

    def __init__(
        self,
        *,
        order_log_path: Optional[str] = None,
        fill_log_path: Optional[str] = None,
        rejection_log_path: Optional[str] = None,
        account_equity: Optional[float] = None,
    ) -> None:
        self.order_log_path = Path(order_log_path) if order_log_path else default_order_log_path()
        self.fill_log_path = Path(fill_log_path) if fill_log_path else default_fill_log_path()
        self.rejection_log_path = (
            Path(rejection_log_path) if rejection_log_path else default_rejection_log_path()
        )
        if account_equity is None:
            account_equity = float(os.getenv("FAKE_LIVE_EQUITY", "10000"))
        self._account_equity = float(account_equity)
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def submit_order(self, order: LiveOrder) -> LiveFill:
        if not self._connected:
            raise RuntimeError(
                "FakeLiveBrokerAdapter.submit_order called before connect()"
            )
        write_live_order(order, self.order_log_path)
        ts = time.time()
        iso = datetime.now(timezone.utc).isoformat()
        fill = LiveFill(
            order_id=order.order_id,
            ts=ts,
            timestamp=iso,
            side=order.side,
            fill_price=order.entry,
            qty=order.qty,
            slippage=0.0,
            status="filled",
        )
        write_live_fill(fill, self.fill_log_path)
        return fill

    def cancel_order(self, order_id: str) -> bool:
        return False

    def positions(self) -> list[dict]:
        return []

    def account_equity(self) -> float:
        return self._account_equity
