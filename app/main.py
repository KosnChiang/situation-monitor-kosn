"""FastAPI entrypoint.

Endpoints:
    GET  /health                  -> liveness + mode info
    POST /signal                  -> manual signal injection (mock-only, legacy)
    POST /webhook/tradingview     -> TradingView alert webhook (Phase 6.A-4,
                                     mock-only via ExecutorRouter)
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from approval.approval_gate import ApprovalGate  # noqa: E402
from approval.models import ApprovalDecision  # noqa: E402
from app.webhook_ai_swing import (  # noqa: E402
    TVAlertPayload,
    process_alert as ai_swing_process_alert,
    verify_secret as ai_swing_verify_secret,
    write_webhook_event as ai_swing_write_event,
    _redact_for_log as ai_swing_redact_for_log,
)
from app.webhook_tradingview import (  # noqa: E402
    TVPayload,
    default_webhook_log_path,
    payload_to_signal,
    redact_for_log,
    verify_secret,
    write_webhook_log,
)
from executor.executor_router import (  # noqa: E402
    ExecutorRouter,
    LiveTradingForbidden as RouterLiveTradingForbidden,
)
from executor.mock_executor import MockExecutor  # noqa: E402
from notify.telegram_bot import TelegramBot  # noqa: E402
from risk.risk_gate import RiskGate, LiveTradingForbidden  # noqa: E402
from strategy.fibo_mob_v2 import Signal  # noqa: E402


try:
    risk_gate = RiskGate()
except LiveTradingForbidden as e:
    raise SystemExit(str(e))

executor = MockExecutor()
telegram = TelegramBot()

app = FastAPI(title="AI Fibo Vision Trader (Mock)", version="0.2.0")


class SignalPayload(BaseModel):
    side: Literal["LONG", "SHORT", "FLAT"]
    entry: float
    stop: float
    target: float
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = "webhook"


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "live_trading": os.getenv("LIVE_TRADING", "false"),
        "execution_mode": os.getenv("EXECUTION_MODE", "mock"),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES", ""),
        "signals_today": risk_gate.used,
        "telegram_enabled": telegram.enabled,
        "tradingview_webhook_enabled": bool(
            os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "").strip()
        ),
    }


@app.post("/signal")
def post_signal(payload: SignalPayload) -> dict:
    sig = Signal(
        side=payload.side,
        entry=payload.entry,
        stop=payload.stop,
        target=payload.target,
        confidence=payload.confidence,
        reason=payload.reason,
    )
    decision = risk_gate.check(sig)
    if not decision.approved:
        raise HTTPException(status_code=409, detail=decision.reason)
    fill = executor.submit(sig)
    if telegram.enabled:
        telegram.send(f"[MOCK] {fill.side} entry={fill.entry} conf={fill.confidence:.2f}")
    return {"ok": True, "fill": fill.__dict__, "mode": "mock"}


def _synthesize_auto_approval() -> ApprovalDecision:
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    return ApprovalDecision(
        request_id=f"webhook-auto-{int(ts * 1000)}",
        approved=True,
        reason="auto_approved",
        operator_chat_id="webhook-auto",
        decided_ts=ts,
        decided_timestamp=iso,
        elapsed_ms=0.0,
    )


@app.post("/webhook/tradingview")
def webhook_tradingview(payload: TVPayload) -> dict:
    """Phase 6.A-4 TradingView webhook adapter.

    Flow: secret check -> RiskGate -> (optional) ApprovalGate ->
    ExecutorRouter. Always writes one audit row to logs/webhooks.jsonl
    (env TRADINGVIEW_WEBHOOK_LOG); the row's secret field is ALWAYS
    the redaction token, never the real value.

    ExecutorRouter is constructed per request so EXECUTION_MODE env
    is honoured fresh each call. If EXECUTION_MODE=live is somehow
    set, Router raises and we return 503 (no route to live exists
    in this build).
    """
    log_path = default_webhook_log_path()
    expected = (os.getenv("TRADINGVIEW_WEBHOOK_SECRET", "") or "").strip()

    if not expected:
        write_webhook_log(redact_for_log(payload, verdict="disabled"), log_path)
        raise HTTPException(status_code=503, detail="webhook disabled")

    if not verify_secret(payload.secret, expected):
        write_webhook_log(redact_for_log(payload, verdict="unauthorized"), log_path)
        raise HTTPException(status_code=401, detail="unauthorized")

    sig = payload_to_signal(payload)

    decision_risk = risk_gate.check(sig)
    if not decision_risk.approved:
        write_webhook_log(
            redact_for_log(
                payload,
                verdict="risk_rejected",
                skip_reason=decision_risk.reason,
            ),
            log_path,
        )
        raise HTTPException(status_code=409, detail=decision_risk.reason)

    require_approval = (
        os.getenv("TRADINGVIEW_REQUIRE_APPROVAL", "false") or ""
    ).strip().lower() == "true"

    if require_approval:
        gate = ApprovalGate(
            timeout_seconds=float(os.getenv("APPROVAL_TIMEOUT_SECONDS", "5")),
            log_path=os.getenv("APPROVALS_LOG"),
        )
        req = gate.request(
            symbol=payload.ticker,
            side=sig.side,
            entry=sig.entry,
            stop=sig.stop,
            target=sig.target,
            confidence=sig.confidence,
            reason=sig.reason,
        )
        approval = gate.await_decision(req)
    else:
        approval = _synthesize_auto_approval()

    try:
        router = ExecutorRouter(log_path=os.getenv("ROUTER_DECISIONS_LOG"))
    except RouterLiveTradingForbidden as e:
        write_webhook_log(
            redact_for_log(
                payload,
                verdict="router_init_failed",
                skip_reason=str(e),
            ),
            log_path,
        )
        raise HTTPException(status_code=503, detail=str(e))

    outcome = router.route(sig, approval, symbol=payload.ticker)

    write_webhook_log(
        redact_for_log(
            payload,
            verdict="routed" if outcome.submitted else "skipped",
            outcome_request_id=outcome.request_id,
            skip_reason=outcome.skip_reason,
        ),
        log_path,
    )

    return {"ok": outcome.submitted, "outcome": outcome.to_log_dict()}


@app.post("/webhook/ai-swing")
def webhook_ai_swing(payload: TVAlertPayload) -> dict:
    """Phase 7.A AI-swing webhook. Pine-script alerts come here, get
    converted to ChartContext, fed to the deterministic engine, and
    (for ENTRY) auto-dispatched via FakeLiveBrokerAdapter.

    Secret env: AI_SWING_WEBHOOK_SECRET. Blank -> 503 disabled.
    """
    expected = (os.getenv("AI_SWING_WEBHOOK_SECRET", "") or "").strip()
    if not expected:
        ai_swing_write_event(ai_swing_redact_for_log(payload, verdict="disabled"))
        raise HTTPException(status_code=503, detail="ai-swing webhook disabled")

    if not ai_swing_verify_secret(payload.secret, expected):
        ai_swing_write_event(ai_swing_redact_for_log(payload, verdict="unauthorized"))
        raise HTTPException(status_code=401, detail="unauthorized")

    summary = ai_swing_process_alert(
        payload,
        mode=(os.getenv("EXECUTION_MODE", "paper") or "paper"),
    )
    return summary
