"""FastAPI entrypoint.

Endpoints:
    GET  /health   -> liveness + mode info
    POST /signal   -> manual / webhook signal injection (mock-only)
"""
from __future__ import annotations

import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

load_dotenv()

from executor.mock_executor import MockExecutor  # noqa: E402
from risk.risk_gate import RiskGate, LiveTradingForbidden  # noqa: E402
from strategy.fibo_mob_v2 import Signal  # noqa: E402
from notify.telegram_bot import TelegramBot  # noqa: E402


try:
    risk_gate = RiskGate()
except LiveTradingForbidden as e:
    raise SystemExit(str(e))

executor = MockExecutor()
telegram = TelegramBot()

app = FastAPI(title="AI Fibo Vision Trader (Mock)", version="0.1.0")


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
