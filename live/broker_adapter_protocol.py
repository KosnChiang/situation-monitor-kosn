"""Live broker adapter protocol (Phase 6.B-1).

The contract that any live execution adapter -- in-repo (the fake
test adapter) or out-of-tree (a real CTPro / IB / MT5 binding,
loaded by path in a later phase) -- must satisfy.

Named ``LiveBrokerAdapterProtocol`` rather than ``LiveBrokerAdapter``
to keep the Phase 6.A-2 structural guard (no concrete
``class LiveBrokerAdapter`` in the source tree) trivially satisfied.

The repo deliberately ships NO real implementation -- the Protocol
defines the interface, ``FakeLiveBrokerAdapter`` exercises the live
path for tests, and any real broker binding lives out-of-tree.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from live.models import LiveFill, LiveOrder


@runtime_checkable
class LiveBrokerAdapterProtocol(Protocol):
    """Minimal interface every live execution backend must implement."""

    name: str

    def connect(self) -> None: ...

    def submit_order(self, order: LiveOrder) -> LiveFill: ...

    def cancel_order(self, order_id: str) -> bool: ...

    def positions(self) -> list[dict]: ...

    def account_equity(self) -> float: ...

    def disconnect(self) -> None: ...
