"""MicroLiveGate (Phase 6.B-1).

Hard pre-order risk gate for live execution. Every check is
fail-loudly: if anything is wrong, the order is rejected and a
``LiveRejection`` is written to ``logs/live_rejections.jsonl``.

Checks (any one failing => reject):
  * symbol must be in ALLOWED_SYMBOLS
  * qty <= MAX_POSITION_SIZE
  * daily_trade_count < MAX_DAILY_TRADES
  * side in {LONG, SHORT}                       (FLAT / WATCH rejected)
  * stop and target must both be set and != entry
  * confidence >= MIN_LIVE_CONFIDENCE
  * per-trade risk (|entry-stop|*qty) <= MAX_LOSS_PER_TRADE
  * daily_loss + per-trade risk <= MAX_DAILY_LOSS
  * stop distance / entry sanity check (<= 50% by default)

Env overrides (all optional; constructor kwargs take precedence):
  ALLOWED_SYMBOLS, MAX_POSITION_SIZE, MAX_DAILY_TRADES,
  MAX_LOSS_PER_TRADE, MAX_DAILY_LOSS, MIN_LIVE_CONFIDENCE
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ai_decision.models import AIDecision
from live.audit_log import default_rejection_log_path, write_live_rejection
from live.models import LiveRejection


@dataclass(frozen=True)
class MicroLiveResult:
    approved: bool
    reasons: list[str]


class MicroLiveGate:
    def __init__(
        self,
        *,
        allowed_symbols: Optional[list[str]] = None,
        max_position_size: Optional[float] = None,
        max_daily_trades: Optional[int] = None,
        max_loss_per_trade: Optional[float] = None,
        max_daily_loss: Optional[float] = None,
        min_live_confidence: Optional[float] = None,
        max_stop_distance_pct: float = 0.5,
        rejection_log_path: Optional[str] = None,
    ) -> None:
        self.allowed_symbols = (
            list(allowed_symbols)
            if allowed_symbols is not None
            else self._parse_env_list("ALLOWED_SYMBOLS")
        )
        self.max_position_size = float(
            max_position_size
            if max_position_size is not None
            else os.getenv("MAX_POSITION_SIZE", "1")
        )
        self.max_daily_trades = int(
            max_daily_trades
            if max_daily_trades is not None
            else os.getenv("MAX_DAILY_TRADES", "3")
        )
        self.max_loss_per_trade = float(
            max_loss_per_trade
            if max_loss_per_trade is not None
            else os.getenv("MAX_LOSS_PER_TRADE", "10")
        )
        self.max_daily_loss = float(
            max_daily_loss
            if max_daily_loss is not None
            else os.getenv("MAX_DAILY_LOSS", "30")
        )
        self.min_live_confidence = float(
            min_live_confidence
            if min_live_confidence is not None
            else os.getenv("MIN_LIVE_CONFIDENCE", "0.7")
        )
        self.max_stop_distance_pct = float(max_stop_distance_pct)
        self.rejection_log_path = (
            Path(rejection_log_path)
            if rejection_log_path
            else default_rejection_log_path()
        )

    @staticmethod
    def _parse_env_list(name: str) -> list[str]:
        raw = (os.getenv(name, "") or "").strip()
        return [s.strip() for s in raw.split(",") if s.strip()]

    def check(
        self,
        *,
        decision: AIDecision,
        qty: float,
        daily_trade_count: int,
        daily_loss: float,
        order_id: Optional[str] = None,
    ) -> MicroLiveResult:
        reasons: list[str] = []

        if decision.side not in ("LONG", "SHORT"):
            reasons.append(f"side_not_tradable:{decision.side}")

        if self.allowed_symbols and decision.symbol not in self.allowed_symbols:
            reasons.append(f"symbol_not_allowed:{decision.symbol}")
        elif not self.allowed_symbols:
            reasons.append("allowed_symbols_empty")

        if qty > self.max_position_size:
            reasons.append(f"qty_exceeds_max:{qty}>{self.max_position_size}")

        if daily_trade_count >= self.max_daily_trades:
            reasons.append(
                f"daily_trades_at_cap:{daily_trade_count}>={self.max_daily_trades}"
            )

        if decision.stop is None or decision.stop == decision.entry:
            reasons.append("missing_or_zero_stop_distance")

        if decision.target is None or decision.target == decision.entry:
            reasons.append("missing_or_zero_target_distance")

        if decision.confidence < self.min_live_confidence:
            reasons.append(
                f"confidence_below_min:{decision.confidence}<{self.min_live_confidence}"
            )

        if decision.stop is not None and decision.entry is not None:
            risk_per_unit = abs(decision.entry - decision.stop)
            risk_total = risk_per_unit * qty
            if risk_total > self.max_loss_per_trade:
                reasons.append(
                    f"per_trade_risk_exceeds_max:{risk_total:.4f}>{self.max_loss_per_trade}"
                )

            if decision.entry != 0:
                pct = risk_per_unit / abs(decision.entry)
                if pct > self.max_stop_distance_pct:
                    reasons.append(
                        f"stop_distance_unreasonable:{pct:.4f}>{self.max_stop_distance_pct}"
                    )

            potential_total_loss = daily_loss + risk_total
            if potential_total_loss > self.max_daily_loss:
                reasons.append(
                    f"daily_loss_cap_would_exceed:{potential_total_loss:.4f}>{self.max_daily_loss}"
                )

        if reasons:
            ts = time.time()
            iso = datetime.now(timezone.utc).isoformat()
            rejection = LiveRejection(
                ts=ts,
                timestamp=iso,
                decision_id=decision.decision_id,
                order_id=order_id,
                symbol=decision.symbol,
                side=decision.side,
                reason="; ".join(reasons),
                rejection_layer="micro_live_gate",
            )
            write_live_rejection(rejection, self.rejection_log_path)
            return MicroLiveResult(approved=False, reasons=reasons)

        return MicroLiveResult(approved=True, reasons=[])
