"""LivePipeline — Phase 6.B-2 dry-run live path orchestrator.

Wires the Phase 6.B-1 building blocks into a single end-to-end flow:

  AI decision JSON
    -> ai_decision.validator           writes ai_decisions.jsonl
    -> LiveUnlockGate.unlock           (9 conditions)
    -> KillSwitch.is_active            (file / env / session / auto-trip)
    -> tradable check                  (FLAT / WATCH / low confidence)
    -> MicroLiveGate.check             writes live_rejections.jsonl
    -> FakeLiveBrokerAdapter.submit    writes live_orders.jsonl +
                                       live_fills.jsonl

Each failure mode emits a single ``LiveRejection`` row to
``logs/live_rejections.jsonl`` with ``rejection_layer`` set to one of:

  ai_decision_validator | live_unlock_gate | kill_switch |
  not_tradable          | micro_live_gate  | adapter

Mock-only:
  * Pipeline imports ONLY the in-repo fake adapter; no path-based
    loader, no broker SDK, no outbound HTTP, no broker credential env
    read. The structural guard in tests/test_no_live_trading_phase6b2.py
    enforces this.
  * The Live unlock gate's two adapter-discovery envs are both
    accepted by the gate (path-based or fake-flag), but this pipeline
    always uses the fake adapter handed to it at construction time.
    Dynamic loading is a Phase 6.B-3 concern, not this one.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ai_decision.models import AIDecision
from ai_decision.validator import validate_ai_decision
from live.audit_log import default_rejection_log_path, write_live_rejection
from live.broker_adapter_protocol import LiveBrokerAdapterProtocol
from live.kill_switch import KillSwitch
from live.live_unlock_gate import LiveUnlockForbidden, LiveUnlockGate
from live.micro_live_gate import MicroLiveGate
from live.models import LiveFill, LiveOrder, LiveRejection


PIPELINE_LAYERS = (
    "ai_decision_validator",
    "live_unlock_gate",
    "kill_switch",
    "not_tradable",
    "micro_live_gate",
    "adapter",
)


@dataclass(frozen=True)
class PipelineResult:
    outcome: str
    exit_code: int
    decision_id: Optional[str]
    order_id: Optional[str]
    decision: Optional[AIDecision]
    fill: Optional[LiveFill]
    rejection_reason: Optional[str]

    def summary(self) -> dict:
        return {
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "decision_id": self.decision_id,
            "order_id": self.order_id,
            "fill_price": self.fill.fill_price if self.fill else None,
            "rejection_reason": self.rejection_reason,
        }


class LivePipeline:
    def __init__(
        self,
        *,
        adapter: LiveBrokerAdapterProtocol,
        kill_switch: Optional[KillSwitch] = None,
        unlock_gate: Optional[LiveUnlockGate] = None,
        micro_gate: Optional[MicroLiveGate] = None,
        min_trade_confidence: float = 0.7,
        ai_decisions_log: Optional[str | Path] = None,
        rejections_log: Optional[str | Path] = None,
    ) -> None:
        if adapter is None:
            raise ValueError(
                "LivePipeline requires a LiveBrokerAdapterProtocol-compatible "
                "adapter; the pipeline deliberately does NOT auto-resolve. "
                "The caller (CLI or test) chooses fake vs path-loaded."
            )
        self.adapter = adapter
        self.unlock_gate = unlock_gate or LiveUnlockGate()
        self.kill_switch = kill_switch or KillSwitch()
        self.rejections_log_path = (
            Path(rejections_log) if rejections_log else default_rejection_log_path()
        )
        self.micro_gate = micro_gate or MicroLiveGate(
            rejection_log_path=str(self.rejections_log_path)
        )
        self.min_trade_confidence = float(min_trade_confidence)
        self.ai_decisions_log = Path(ai_decisions_log) if ai_decisions_log else None

    @staticmethod
    def _maybe_str(raw: Any, key: str) -> Optional[str]:
        if isinstance(raw, dict) and isinstance(raw.get(key), str):
            return raw[key]
        return None

    @staticmethod
    def _maybe_decision_id(raw: Any) -> Optional[str]:
        if isinstance(raw, dict):
            val = raw.get("decision_id")
            if isinstance(val, str):
                return val
        return None

    def _now(self) -> tuple[float, str]:
        return time.time(), datetime.now(timezone.utc).isoformat()

    def _write_rejection(
        self,
        *,
        layer: str,
        decision_id: Optional[str],
        order_id: Optional[str],
        symbol: str,
        side: str,
        reason: str,
    ) -> LiveRejection:
        ts, iso = self._now()
        rej = LiveRejection(
            ts=ts,
            timestamp=iso,
            decision_id=decision_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            reason=reason,
            rejection_layer=layer,
        )
        write_live_rejection(rej, self.rejections_log_path)
        return rej

    def run(
        self,
        ai_decision_raw: Any,
        *,
        qty: float,
        daily_trade_count: int = 0,
        daily_loss: float = 0.0,
    ) -> PipelineResult:
        # ---- Step 1: validate the AI decision JSON ----------------
        validation = validate_ai_decision(
            ai_decision_raw,
            min_trade_confidence=self.min_trade_confidence,
            log_path=self.ai_decisions_log,
        )
        if not validation.valid:
            reason = "; ".join(validation.errors)
            symbol = self._maybe_str(ai_decision_raw, "symbol") or "UNKNOWN"
            side = self._maybe_str(ai_decision_raw, "side") or "UNKNOWN"
            self._write_rejection(
                layer="ai_decision_validator",
                decision_id=self._maybe_decision_id(ai_decision_raw),
                order_id=None,
                symbol=symbol,
                side=side,
                reason=reason,
            )
            return PipelineResult(
                outcome="ai_invalid",
                exit_code=2,
                decision_id=self._maybe_decision_id(ai_decision_raw),
                order_id=None,
                decision=None,
                fill=None,
                rejection_reason=reason,
            )

        decision = validation.decision
        assert decision is not None  # guaranteed by validation.valid

        # ---- Step 2: LiveUnlockGate -------------------------------
        try:
            self.unlock_gate.unlock()
        except LiveUnlockForbidden as e:
            self._write_rejection(
                layer="live_unlock_gate",
                decision_id=decision.decision_id,
                order_id=None,
                symbol=decision.symbol,
                side=decision.side,
                reason=str(e),
            )
            return PipelineResult(
                outcome="unlock_refused",
                exit_code=3,
                decision_id=decision.decision_id,
                order_id=None,
                decision=decision,
                fill=None,
                rejection_reason=str(e),
            )

        # ---- Step 3: KillSwitch -----------------------------------
        active, reasons = self.kill_switch.is_active()
        if active:
            reason = "; ".join(reasons)
            self._write_rejection(
                layer="kill_switch",
                decision_id=decision.decision_id,
                order_id=None,
                symbol=decision.symbol,
                side=decision.side,
                reason=reason,
            )
            return PipelineResult(
                outcome="killed",
                exit_code=4,
                decision_id=decision.decision_id,
                order_id=None,
                decision=decision,
                fill=None,
                rejection_reason=reason,
            )

        # ---- Step 4: tradable check (side / confidence) -----------
        if not validation.tradable:
            reason = "; ".join(validation.warnings) or f"side={decision.side}"
            self._write_rejection(
                layer="not_tradable",
                decision_id=decision.decision_id,
                order_id=None,
                symbol=decision.symbol,
                side=decision.side,
                reason=reason,
            )
            return PipelineResult(
                outcome="not_tradable",
                exit_code=7,
                decision_id=decision.decision_id,
                order_id=None,
                decision=decision,
                fill=None,
                rejection_reason=reason,
            )

        # ---- Step 5: MicroLiveGate --------------------------------
        # MicroLiveGate writes its own rejection row internally, so the
        # pipeline does NOT double-write here. Same rejections_log path
        # was passed to MicroLiveGate at construction.
        micro = self.micro_gate.check(
            decision=decision,
            qty=qty,
            daily_trade_count=daily_trade_count,
            daily_loss=daily_loss,
        )
        if not micro.approved:
            reason = "; ".join(micro.reasons)
            return PipelineResult(
                outcome="micro_rejected",
                exit_code=5,
                decision_id=decision.decision_id,
                order_id=None,
                decision=decision,
                fill=None,
                rejection_reason=reason,
            )

        # ---- Step 6: build LiveOrder + submit ---------------------
        order_id = str(uuid.uuid4())
        ts, iso = self._now()
        order = LiveOrder(
            order_id=order_id,
            ts=ts,
            timestamp=iso,
            decision_id=decision.decision_id,
            symbol=decision.symbol,
            side=decision.side,
            qty=float(qty),
            entry=decision.entry,
            stop=decision.stop,
            target=decision.target,
            confidence=decision.confidence,
            reason=decision.reason,
        )

        self.adapter.connect()
        try:
            fill = self.adapter.submit_order(order)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            self._write_rejection(
                layer="adapter",
                decision_id=decision.decision_id,
                order_id=order_id,
                symbol=decision.symbol,
                side=decision.side,
                reason=reason,
            )
            return PipelineResult(
                outcome="adapter_error",
                exit_code=6,
                decision_id=decision.decision_id,
                order_id=order_id,
                decision=decision,
                fill=None,
                rejection_reason=reason,
            )
        finally:
            self.adapter.disconnect()

        return PipelineResult(
            outcome="filled",
            exit_code=0,
            decision_id=decision.decision_id,
            order_id=order_id,
            decision=decision,
            fill=fill,
            rejection_reason=None,
        )
