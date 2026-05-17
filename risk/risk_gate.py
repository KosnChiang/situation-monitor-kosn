"""Risk gate. Final guardrail before any executor sees a signal.

It enforces the mock-only invariant: any attempt to run with
LIVE_TRADING=true or EXECUTION_MODE!=mock is refused at construction.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from strategy.fibo_mob_v2 import Signal


class LiveTradingForbidden(RuntimeError):
    """Raised when configuration tries to enable real-money trading."""


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    signal: Signal


class RiskGate:
    def __init__(
        self,
        max_daily_signals: int = 20,
        min_confidence: float = 0.55,
        live_trading_env: str = "LIVE_TRADING",
        execution_mode_env: str = "EXECUTION_MODE",
    ) -> None:
        live = os.getenv(live_trading_env, "false").strip().lower()
        mode = os.getenv(execution_mode_env, "mock").strip().lower()
        if live != "false" or mode != "mock":
            raise LiveTradingForbidden(
                f"This build is mock-only. Got {live_trading_env}={live!r}, "
                f"{execution_mode_env}={mode!r}. Refusing to start."
            )
        self.max_daily_signals = max_daily_signals
        self.min_confidence = min_confidence
        self._count = 0

    def check(self, signal: Signal) -> RiskDecision:
        if signal.side == "FLAT":
            return RiskDecision(False, "flat signal", signal)
        if signal.confidence < self.min_confidence:
            return RiskDecision(False, f"confidence {signal.confidence:.2f} < {self.min_confidence}", signal)
        if self._count >= self.max_daily_signals:
            return RiskDecision(False, "daily signal cap reached", signal)
        self._count += 1
        return RiskDecision(True, "approved", signal)

    @property
    def used(self) -> int:
        return self._count
