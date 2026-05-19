"""BrokerAdapter Protocol + in-repo adapters (Phase 6.A-2).

Only the contract and the two in-repo adapters live here:

  * MockBrokerAdapter  — wraps the existing MockExecutor. Pure
    delegation, no behaviour change. ``name == "mock"``.
  * PaperBrokerAdapter — Phase 6.A-2 stub. ``submit()`` raises
    ``NotImplementedError`` so ExecutorRouter records the route as
    skipped. Phase 6.A-3 will implement it against logs/quotes.jsonl.
    ``name == "paper"``.

LiveBrokerAdapter is NOT in this repo. The Live execution path lives
out-of-tree and is loaded via ``LIVE_BROKER_ADAPTER_PATH`` in a later
phase. ``tests/test_no_live_trading_phase6a2.py`` asserts that no
concrete ``class LiveBrokerAdapter`` exists in the source tree.

Strictly mock-only:
  * Imports only stdlib + existing in-repo modules.
  * No broker SDK import.
  * No outbound HTTP library.
  * No broker credential env read.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from executor.mock_executor import MockExecutor, MockFill
from strategy.fibo_mob_v2 import Signal


@runtime_checkable
class BrokerAdapter(Protocol):
    """Minimal contract every executor target must satisfy.

    Note: ``runtime_checkable`` only verifies that ``submit`` is
    callable at isinstance time -- the ``name`` data attribute is
    enforced separately by ``tests/test_broker_adapter_protocol.py``.
    """

    name: str

    def submit(self, signal: Signal) -> MockFill: ...


class MockBrokerAdapter:
    """Wraps the existing MockExecutor without modifying it."""

    name: str = "mock"

    def __init__(self, executor: MockExecutor | None = None) -> None:
        self._executor = executor or MockExecutor()

    def submit(self, signal: Signal) -> MockFill:
        return self._executor.submit(signal)


class PaperBrokerAdapter:
    """Phase 6.A-2 stub for a future quote-feed-driven simulator.

    ``submit()`` deliberately raises ``NotImplementedError`` so that
    ``ExecutorRouter.route()`` catches it and records the outcome as
    ``submitted=False, skip_reason="paper_not_implemented"`` without
    polluting ``logs/trades.jsonl``.
    """

    name: str = "paper"

    def submit(self, signal: Signal) -> MockFill:  # noqa: ARG002
        raise NotImplementedError(
            "PaperBrokerAdapter is a Phase 6.A-2 stub. "
            "Phase 6.A-3 will implement it against logs/quotes.jsonl. "
            "Run with EXECUTION_MODE=mock to route through MockExecutor."
        )
