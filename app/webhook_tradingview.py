"""TradingView webhook adapter (Phase 6.A-4, mock-only).

Pure helpers: Pydantic schema, secret verification, payload-to-Signal
conversion, redacted webhook audit log. The orchestration (RiskGate
-> ApprovalGate -> ExecutorRouter) lives in ``app/main.py``'s
``/webhook/tradingview`` route; this module deliberately stays
broker-free and FastAPI-free so it can be unit-tested without an app
fixture.

Mock-only:
  * Reads only this layer's own env names (the webhook secret + log
    path + require-approval flag). Never reads any TradingView
    session / auth token, never reads any broker credential.
  * No broker SDK import.
  * No outbound HTTP library.
  * The webhook audit log NEVER contains the real secret -- it is
    replaced by ``"***"`` before write.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from strategy.fibo_mob_v2 import Signal


REDACTED = "***"


class TVPayload(BaseModel):
    """Schema for an inbound TradingView alert POST body.

    ``stop`` / ``target`` are optional; when missing they default to
    ``price`` so RiskGate and downstream Signal still have numeric
    fields. ``confidence`` defaults to 0.55, the same MVP threshold
    used elsewhere in the repo.
    """

    secret: str = Field(min_length=1, max_length=512)
    ticker: str = Field(min_length=1, max_length=64)
    side: Literal["LONG", "SHORT", "FLAT"]
    price: float
    stop: Optional[float] = None
    target: Optional[float] = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.55)
    reason: str = "tradingview-alert"
    strategy_tag: Optional[str] = None


def verify_secret(presented: str, expected: str) -> bool:
    """Constant-time secret comparison. Empty expected/presented returns False."""
    if not expected or not presented:
        return False
    return hmac.compare_digest(presented, expected)


def payload_to_signal(payload: TVPayload) -> Signal:
    """Convert a TV payload to internal strategy.fibo_mob_v2.Signal.

    Missing stop/target default to price (preserves RiskGate's numeric
    contract without inventing levels)."""
    return Signal(
        side=payload.side,
        entry=float(payload.price),
        stop=float(payload.stop if payload.stop is not None else payload.price),
        target=float(payload.target if payload.target is not None else payload.price),
        confidence=float(payload.confidence),
        reason=f"tv:{payload.strategy_tag or payload.reason}",
    )


def redact_for_log(
    payload: TVPayload,
    *,
    verdict: str,
    outcome_request_id: Optional[str] = None,
    skip_reason: Optional[str] = None,
    mode: str = "mock",
) -> dict:
    """Build a webhooks.jsonl row. ``secret`` is always replaced with
    the redaction token regardless of what the payload contained."""
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    return {
        "event_type": "webhook_inbound",
        "ts": ts,
        "timestamp": iso,
        "ticker": payload.ticker,
        "side": payload.side,
        "price": payload.price,
        "stop": payload.stop,
        "target": payload.target,
        "confidence": payload.confidence,
        "reason": payload.reason,
        "strategy_tag": payload.strategy_tag,
        "secret": REDACTED,
        "verdict": verdict,
        "outcome_request_id": outcome_request_id,
        "skip_reason": skip_reason,
        "mode": mode,
    }


def write_webhook_log(record: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def default_webhook_log_path() -> Path:
    return Path(os.getenv("TRADINGVIEW_WEBHOOK_LOG", "logs/webhooks.jsonl"))
