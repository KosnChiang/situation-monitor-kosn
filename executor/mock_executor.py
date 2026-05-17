"""Mock executor. Writes signals to logs/trades.jsonl.

This module deliberately does NOT import any broker SDK and contains no
network calls. The presence of any function whose name suggests live
order submission is forbidden by tests in tests/test_mock_only.py.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from strategy.fibo_mob_v2 import Signal


@dataclass
class MockFill:
    ts: float
    side: str
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str
    mode: str = "mock"


class MockExecutor:
    def __init__(self, log_path: str | None = None) -> None:
        self.log_path = Path(log_path or os.getenv("TRADES_LOG", "logs/trades.jsonl"))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def submit(self, signal: Signal) -> MockFill:
        # Hard reassert mock mode at the executor boundary.
        if os.getenv("LIVE_TRADING", "false").lower() != "false":
            raise RuntimeError("Refusing to submit: LIVE_TRADING is enabled.")
        if os.getenv("EXECUTION_MODE", "mock").lower() != "mock":
            raise RuntimeError("Refusing to submit: EXECUTION_MODE is not mock.")

        fill = MockFill(
            ts=time.time(),
            side=signal.side,
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            confidence=signal.confidence,
            reason=signal.reason,
        )
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(fill), ensure_ascii=False) + "\n")
        return fill
