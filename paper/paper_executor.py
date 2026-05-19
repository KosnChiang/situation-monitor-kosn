"""PaperExecutor (Phase 6.A-3 minimal v1).

Simulates fills against operator-supplied quotes. ``submit()`` opens
a position at the signal's entry price (no slippage in v1). ``tick()``
takes a quote dict with at least a ``last`` field and closes any open
position whose stop or target has been reached.

Strictly mock-only:
  * Refuses construction unless ``LIVE_TRADING`` env is ``false``.
  * Writes only to ``logs/paper_trades.jsonl`` (env
    ``PAPER_TRADES_LOG`` overrides). The MockExecutor's own log
    file is never touched by this module -- the structural guard
    in tests/test_no_live_trading_phase6a3.py enforces this.
  * Every record carries ``mode="paper"``.
  * Imports only stdlib + in-repo ``paper.models`` +
    ``strategy.fibo_mob_v2``. No broker SDK, no outbound HTTP,
    no broker credential env read.

What v1 deliberately does NOT do:
  * No slippage model (entry = signal.entry, exit = stop or target
    exactly).
  * No expiry / max-hold-seconds (positions stay open until tick()
    crosses stop or target).
  * No paper_account.json snapshot.
  * No QuoteTail file reader -- the caller passes quote dicts directly.
  * No open-position persistence across process restarts.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from paper.models import PaperFill, PaperPosition
from strategy.fibo_mob_v2 import Signal


class LiveTradingForbidden(RuntimeError):
    """Raised when PaperExecutor detects an attempt to enable live trading."""


def _now() -> tuple[float, str]:
    return time.time(), datetime.now(timezone.utc).isoformat()


class PaperExecutor:
    def __init__(
        self,
        *,
        log_path: Optional[str] = None,
        live_trading_env: str = "LIVE_TRADING",
    ) -> None:
        live = (os.getenv(live_trading_env, "false") or "").strip().lower()
        if live != "false":
            raise LiveTradingForbidden(
                f"PaperExecutor is mock-only. Got {live_trading_env}={live!r}. "
                f"Refusing to start."
            )
        self.log_path = Path(
            log_path or os.getenv("PAPER_TRADES_LOG", "logs/paper_trades.jsonl")
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._positions: dict[str, PaperPosition] = {}

    def _write(self, payload: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def submit(self, signal: Signal, *, qty: float = 1.0) -> PaperFill:
        if signal.side not in ("LONG", "SHORT"):
            raise ValueError(
                f"PaperExecutor refuses signal side={signal.side!r}; "
                f"only LONG / SHORT are accepted."
            )
        ts, iso = _now()
        position_id = str(uuid.uuid4())
        pos = PaperPosition(
            position_id=position_id,
            ts_opened=ts,
            timestamp_opened=iso,
            side=signal.side,
            qty=float(qty),
            entry=float(signal.entry),
            stop=float(signal.stop),
            target=float(signal.target),
            confidence=float(signal.confidence),
            reason=signal.reason,
        )
        self._positions[position_id] = pos
        self._write({
            "event_type": "paper_open",
            "ts": ts,
            "timestamp": iso,
            "position_id": position_id,
            "side": pos.side,
            "qty": pos.qty,
            "entry": pos.entry,
            "stop": pos.stop,
            "target": pos.target,
            "confidence": pos.confidence,
            "reason": pos.reason,
            "mode": "paper",
        })
        return PaperFill(
            ts=ts,
            timestamp=iso,
            position_id=position_id,
            side=pos.side,
            entry=pos.entry,
            stop=pos.stop,
            target=pos.target,
            qty=pos.qty,
            confidence=pos.confidence,
            reason=pos.reason,
        )

    def tick(self, quote: dict) -> list[PaperPosition]:
        """Apply one quote to all open positions. Returns the list of
        positions newly closed by this call (may be empty)."""
        if "last" not in quote:
            return []
        last = float(quote["last"])
        newly_closed: list[PaperPosition] = []
        for pid, pos in list(self._positions.items()):
            if pos.status != "open":
                continue
            close_reason: Optional[str] = None
            exit_price: Optional[float] = None
            if pos.side == "LONG":
                if last <= pos.stop:
                    close_reason, exit_price = "stopped", pos.stop
                elif last >= pos.target:
                    close_reason, exit_price = "targeted", pos.target
            elif pos.side == "SHORT":
                if last >= pos.stop:
                    close_reason, exit_price = "stopped", pos.stop
                elif last <= pos.target:
                    close_reason, exit_price = "targeted", pos.target
            if close_reason is None:
                continue
            ts_now, iso_now = _now()
            pnl = (exit_price - pos.entry) * pos.qty
            if pos.side == "SHORT":
                pnl = -pnl
            new_pos = PaperPosition(
                position_id=pos.position_id,
                ts_opened=pos.ts_opened,
                timestamp_opened=pos.timestamp_opened,
                side=pos.side,
                qty=pos.qty,
                entry=pos.entry,
                stop=pos.stop,
                target=pos.target,
                confidence=pos.confidence,
                reason=pos.reason,
                status=close_reason,
                ts_closed=ts_now,
                timestamp_closed=iso_now,
                exit_price=exit_price,
                close_reason=close_reason,
                realized_pnl=pnl,
                hold_seconds=ts_now - pos.ts_opened,
            )
            self._positions[pid] = new_pos
            self._write({
                "event_type": "paper_close",
                "ts_closed": ts_now,
                "timestamp_closed": iso_now,
                "position_id": pid,
                "side": pos.side,
                "qty": pos.qty,
                "entry": pos.entry,
                "exit_price": exit_price,
                "stop": pos.stop,
                "target": pos.target,
                "close_reason": close_reason,
                "realized_pnl": pnl,
                "hold_seconds": ts_now - pos.ts_opened,
                "mode": "paper",
            })
            newly_closed.append(new_pos)
        return newly_closed

    @property
    def open_positions(self) -> list[PaperPosition]:
        return [p for p in self._positions.values() if p.status == "open"]

    @property
    def closed_positions(self) -> list[PaperPosition]:
        return [p for p in self._positions.values() if p.status != "open"]
