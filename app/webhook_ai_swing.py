"""TradingView AI-swing webhook adapter (Phase 7.A).

POST /webhook/ai-swing accepts a TradingView Pine-script alert JSON,
builds a ChartContext, runs the deterministic rule engine, and
records the AISwingDecision. ENTRY actions are dispatched to the
in-repo FakeLiveBrokerAdapter so the full audit chain populates
(live_orders.jsonl + live_fills.jsonl) without a real broker.

Mock-only:
  * Reads only the AI_SWING_WEBHOOK_SECRET env name for auth. Never
    reads broker credentials.
  * No outbound HTTP at module top.
  * Webhook audit log redacts the secret to "***" before writing.
  * EXIT / REDUCE / REVERSE actions in v1 only WRITE the decision;
    no auto-flatten via the bot (operator handles via broker UI; see
    docs/emergency_stop.md).
"""
from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from ai_swing.context import build_chart_context_from_tv_alert
from ai_swing.decision import AISwingDecision
from ai_swing.engine import CooldownState, PositionState, decide
from ai_swing.logging import (
    default_webhook_log_path,
    write_ai_swing_decision,
    write_chart_context,
    write_webhook_event,
)


REDACTED = "***"
TRUTHY = {"1", "true", "yes", "on"}


class TVAlertPayload(BaseModel):
    secret: str = Field(min_length=1, max_length=512)
    source: str = "tradingview"
    strategy: str = "twtx_fibo_v4"
    symbol: str
    timeframe: str = ""
    bar_time: str = ""
    open: float
    high: float
    low: float
    close: float
    prev_ohlc: dict
    fibo: dict
    rsi: dict
    pivot: dict
    signal: dict


def verify_secret(presented: str, expected: str) -> bool:
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def _redact_payload(payload: TVAlertPayload) -> dict:
    d = payload.model_dump()
    d["secret"] = REDACTED
    return d


def _redact_for_log(payload: TVAlertPayload, *,
                    verdict: str,
                    context_id: Optional[str] = None,
                    decision_id: Optional[str] = None,
                    action: Optional[str] = None,
                    skip_reason: Optional[str] = None) -> dict:
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    return {
        "event_type": "ai_swing_webhook_inbound",
        "ts": ts,
        "timestamp": iso,
        "verdict": verdict,
        "context_id": context_id,
        "decision_id": decision_id,
        "action": action,
        "skip_reason": skip_reason,
        "payload": _redact_payload(payload),
        "mode": os.getenv("EXECUTION_MODE", "paper"),
    }


def _position_state_path() -> Path:
    return Path(os.getenv("AI_SWING_POSITION_STATE",
                          "logs/ai_swing_position.json"))


def _cooldown_state_path() -> Path:
    return Path(os.getenv("AI_SWING_COOLDOWN_STATE",
                          "logs/ai_swing_cooldown.json"))


def _load_position_state() -> Optional[PositionState]:
    p = _position_state_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not data:
        return None
    return PositionState(
        side=str(data["side"]),
        entry=float(data["entry"]),
        qty=float(data.get("qty", 1.0)),
        open_order_id=str(data.get("open_order_id", "")),
        initial_stop=float(data.get("initial_stop", 0.0)),
        current_stop=(float(data["current_stop"])
                      if data.get("current_stop") is not None else None),
        bars_held=int(data.get("bars_held", 0)),
    )


def _load_cooldown_state() -> Optional[CooldownState]:
    p = _cooldown_state_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not data or "last_ts" not in data:
        return None
    return CooldownState(
        last_action=str(data.get("last_action", "")),
        last_ts=float(data["last_ts"]),
        cooldown_seconds=float(data.get("cooldown_seconds", 300.0)),
    )


def _save_cooldown_after(d: AISwingDecision) -> None:
    if d.action not in ("ENTRY", "REVERSE"):
        return
    p = _cooldown_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_action": d.action,
        "last_ts": d.ts,
        "cooldown_seconds": float(os.getenv("AI_SWING_COOLDOWN_SECONDS", "300")),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _save_position_after_entry(d: AISwingDecision, *, order_id: str) -> None:
    p = _position_state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "side": d.side,
        "entry": d.entry,
        "qty": d.qty,
        "open_order_id": order_id,
        "initial_stop": d.stop,
        "current_stop": d.stop,
        "bars_held": 0,
    }
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _auto_submit_enabled() -> bool:
    val = (os.getenv("AI_SWING_AUTO_SUBMIT", "true") or "").strip().lower()
    return val in TRUTHY


def _build_live_order_from_decision(d: AISwingDecision):
    from live.models import LiveOrder
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    return LiveOrder(
        order_id=str(uuid.uuid4()),
        ts=ts,
        timestamp=iso,
        decision_id=d.decision_id,
        symbol=d.symbol,
        side=d.side,
        qty=float(d.qty),
        entry=float(d.entry),
        stop=float(d.stop),
        target=float(d.target),
        confidence=float(d.confidence),
        reason=d.reason,
    )


def _dispatch_entry_via_fake(d: AISwingDecision):
    """ENTRY: submit via FakeLiveBrokerAdapter so live_orders.jsonl +
    live_fills.jsonl populate. Returns (order_id, fill) or (None, None)
    on failure."""
    from live.fake_live_adapter import FakeLiveBrokerAdapter
    adapter = FakeLiveBrokerAdapter()
    adapter.connect()
    try:
        order = _build_live_order_from_decision(d)
        fill = adapter.submit_order(order)
        return order.order_id, fill
    finally:
        adapter.disconnect()


def process_alert(payload: TVAlertPayload, *, mode: str = "paper") -> dict:
    """Top-level pipeline. Returns a summary dict for the HTTP response."""
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    context_id = str(uuid.uuid4())

    ctx = build_chart_context_from_tv_alert(
        payload.model_dump(), context_id=context_id, ts=ts, timestamp=iso,
    )
    write_chart_context(ctx)

    position = _load_position_state()
    cooldown = _load_cooldown_state()
    decision = decide(ctx=ctx, position=position, cooldown=cooldown, mode=mode)
    write_ai_swing_decision(decision)

    submitted = False
    order_id: Optional[str] = None
    fill_price: Optional[float] = None

    if decision.action == "ENTRY" and _auto_submit_enabled():
        try:
            order_id, fill = _dispatch_entry_via_fake(decision)
            fill_price = fill.fill_price if fill else None
            if order_id:
                _save_position_after_entry(decision, order_id=order_id)
            submitted = True
        except Exception as exc:
            write_webhook_event(_redact_for_log(
                payload, verdict="dispatch_error",
                context_id=context_id, decision_id=decision.decision_id,
                action=decision.action,
                skip_reason=f"{type(exc).__name__}:{exc}",
            ))

    _save_cooldown_after(decision)

    write_webhook_event(_redact_for_log(
        payload, verdict="processed",
        context_id=context_id, decision_id=decision.decision_id,
        action=decision.action,
    ))

    return {
        "ok": True,
        "context_id": context_id,
        "decision_id": decision.decision_id,
        "action": decision.action,
        "side": decision.side,
        "confidence": decision.confidence,
        "submitted": submitted,
        "order_id": order_id,
        "fill_price": fill_price,
    }
