"""BrokerAdapter Protocol + in-repo adapters (Phase 6.A-2, 6.A-3 paper impl).

Two in-repo adapters live here, both thin wrappers around concrete
executors:

  * MockBrokerAdapter  — wraps ``executor.mock_executor.MockExecutor``.
    ``name == "mock"``. Writes to ``logs/trades.jsonl``.
  * PaperBrokerAdapter — wraps ``paper.paper_executor.PaperExecutor``
    (Phase 6.A-3 minimal v1). ``name == "paper"``. Writes to
    ``logs/paper_trades.jsonl``, NEVER to ``logs/trades.jsonl``.

LiveBrokerAdapter is NOT in this repo. The Live execution path lives
out-of-tree and is loaded via ``LIVE_BROKER_ADAPTER_PATH`` in a later
phase. ``tests/test_no_live_trading_phase6a2.py`` asserts that no
concrete ``class LiveBrokerAdapter`` exists in the source tree.

Strictly mock-only:
  * Imports only stdlib + in-repo modules.
  * No broker SDK import.
  * No outbound HTTP library.
  * No broker credential env read.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from executor.mock_executor import MockExecutor
from paper.paper_executor import PaperExecutor
from strategy.fibo_mob_v2 import Signal


@runtime_checkable
class BrokerAdapter(Protocol):
    """Minimal contract every executor target must satisfy.

    Note: ``runtime_checkable`` only verifies that ``submit`` is
    callable at isinstance time -- the ``name`` data attribute is
    enforced separately by ``tests/test_broker_adapter_protocol.py``.
    """

    name: str

    def submit(self, signal: Signal): ...


class MockBrokerAdapter:
    """Wraps the existing MockExecutor without modifying it."""

    name: str = "mock"

    def __init__(self, executor: MockExecutor | None = None) -> None:
        self._executor = executor or MockExecutor()

    def submit(self, signal: Signal):
        return self._executor.submit(signal)


class PaperBrokerAdapter:
    """Thin wrapper around paper.PaperExecutor (Phase 6.A-3 minimal v1)."""

    name: str = "paper"

    def __init__(self, executor: PaperExecutor | None = None) -> None:
        self._executor = executor or PaperExecutor()

    def submit(self, signal: Signal):
        return self._executor.submit(signal)
