"""ExecutorRouter -- Phase 6.A-2.

Final routing layer between an ``ApprovalDecision`` and a
``BrokerAdapter``. Writes one ``routed_outcome`` row per ``route()``
call to ``logs/router_decisions.jsonl`` (or whatever
``ROUTER_DECISIONS_LOG`` env points at) so the
``approvals.jsonl`` / ``router_decisions.jsonl`` / ``trades.jsonl``
trio can be join-ed on ``request_id`` to reconstruct any decision's
full lifecycle.

Mock-only invariants (defense in depth, redundant with RiskGate and
ApprovalGate but kept locally so a refactor of either can't silently
weaken this layer):

  * Refuses construction unless ``LIVE_TRADING`` env is ``false``.
  * Refuses construction if ``EXECUTION_MODE=live`` is requested.
    Phase 6.A-4 introduces the unlock ritual; until then ``live`` is
    unreachable.
  * ``paper`` mode is allowed but the in-repo adapter is a stub --
    ``submit()`` raises ``NotImplementedError`` and ``route()``
    converts that to a recorded skip.
  * Unknown / unset ``EXECUTION_MODE`` falls back to ``mock`` with a
    logged warning.
  * Imports only stdlib + in-repo modules. No broker SDK. No
    outbound HTTP. No broker credential env read.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from approval.models import ApprovalDecision
from executor.broker_adapter import (
    BrokerAdapter,
    MockBrokerAdapter,
    PaperBrokerAdapter,
)
from strategy.fibo_mob_v2 import Signal


log = logging.getLogger(__name__)


VALID_MODES = {"mock", "paper", "live"}


class LiveTradingForbidden(RuntimeError):
    """Raised when ExecutorRouter detects an attempt to enable live trading."""


@dataclass(frozen=True)
class RoutedOutcome:
    request_id: Optional[str]
    submitted: bool
    skip_reason: Optional[str]
    execution_mode: str
    adapter: str
    ts: float
    timestamp: str
    symbol: Optional[str]
    side: str
    fill_ts: Optional[float]
    mode: str = "mock"

    def to_log_dict(self) -> dict:
        d = asdict(self)
        d["event_type"] = "routed_outcome"
        return d


def _now() -> tuple[float, str]:
    return time.time(), datetime.now(timezone.utc).isoformat()


class ExecutorRouter:
    def __init__(
        self,
        *,
        adapter: Optional[BrokerAdapter] = None,
        execution_mode: Optional[str] = None,
        log_path: Optional[str] = None,
        live_trading_env: str = "LIVE_TRADING",
    ) -> None:
        live = (os.getenv(live_trading_env, "false") or "").strip().lower()
        if live != "false":
            raise LiveTradingForbidden(
                f"ExecutorRouter is mock-only. Got {live_trading_env}={live!r}. "
                f"Refusing to start."
            )

        raw_mode = (
            execution_mode
            if execution_mode is not None
            else (os.getenv("EXECUTION_MODE", "mock") or "mock")
        )
        mode = (raw_mode or "").strip().lower() or "mock"

        if mode == "live":
            raise LiveTradingForbidden(
                "ExecutorRouter refuses EXECUTION_MODE=live in Phase 6.A-2. "
                "The live unlock ritual is introduced in Phase 6.A-4."
            )

        if mode not in VALID_MODES:
            log.warning(
                "ExecutorRouter: unknown EXECUTION_MODE=%r, falling back to 'mock'",
                raw_mode,
            )
            mode = "mock"

        self.execution_mode = mode

        if adapter is not None:
            self._adapter: BrokerAdapter = adapter
        elif mode == "mock":
            self._adapter = MockBrokerAdapter()
        elif mode == "paper":
            self._adapter = PaperBrokerAdapter()
        else:
            self._adapter = MockBrokerAdapter()

        self.log_path = Path(
            log_path or os.getenv("ROUTER_DECISIONS_LOG", "logs/router_decisions.jsonl")
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def adapter_name(self) -> str:
        return getattr(self._adapter, "name", "unknown")

    def _write_outcome(self, outcome: RoutedOutcome) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(outcome.to_log_dict(), ensure_ascii=False) + "\n")

    def _skip(
        self,
        *,
        approval: ApprovalDecision,
        signal: Signal,
        skip_reason: str,
        symbol: Optional[str],
    ) -> RoutedOutcome:
        ts, iso = _now()
        outcome = RoutedOutcome(
            request_id=approval.request_id,
            submitted=False,
            skip_reason=skip_reason,
            execution_mode=self.execution_mode,
            adapter=self.adapter_name,
            ts=ts,
            timestamp=iso,
            symbol=symbol,
            side=signal.side,
            fill_ts=None,
        )
        self._write_outcome(outcome)
        return outcome

    def route(
        self,
        signal: Signal,
        approval: ApprovalDecision,
        *,
        symbol: Optional[str] = None,
    ) -> RoutedOutcome:
        if not approval.approved:
            skip_reason = {
                "manual_reject":         "approval_rejected",
                "approval_timeout":      "approval_timeout",
                "unauthorized_operator": "approval_unauthorized",
            }.get(approval.reason, f"approval_{approval.reason}")
            return self._skip(
                approval=approval,
                signal=signal,
                skip_reason=skip_reason,
                symbol=symbol,
            )

        try:
            fill = self._adapter.submit(signal)
        except NotImplementedError:
            return self._skip(
                approval=approval,
                signal=signal,
                skip_reason=f"{self.adapter_name}_not_implemented",
                symbol=symbol,
            )

        ts, iso = _now()
        outcome = RoutedOutcome(
            request_id=approval.request_id,
            submitted=True,
            skip_reason=None,
            execution_mode=self.execution_mode,
            adapter=self.adapter_name,
            ts=ts,
            timestamp=iso,
            symbol=symbol,
            side=signal.side,
            fill_ts=getattr(fill, "ts", None),
        )
        self._write_outcome(outcome)
        return outcome
