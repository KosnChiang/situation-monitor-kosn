"""Human-in-the-loop approval gate (Phase 6.A-1, mock-only / dry-run).

The gate records every request and every decision (approve, manual
reject, timeout, unauthorized operator) to ``logs/approvals.jsonl``.
It does NOT send any Telegram message in this phase: real Telegram
integration is a later step. The wire format is already shaped so the
bot callback can plug into ``submit_decision`` without changing the
log schema.

Hard guarantees:
  * Refuses construction unless ``LIVE_TRADING`` env is ``false``.
  * Imports only stdlib; no broker SDK; no outbound HTTP library.
  * Never reads any broker credential env name.
  * A decision from an operator not on the allowlist is recorded as
    ``unauthorized_operator`` and closes the request without ever
    flipping ``approved`` to True.
  * Timeout fires deterministically inside ``await_decision`` based on
    monotonic time; the in-memory state and the log file agree.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from approval.models import ApprovalDecision, ApprovalRequest


class LiveTradingForbidden(RuntimeError):
    """Raised when configuration tries to enable real-money trading."""


class ApprovalGate:
    def __init__(
        self,
        *,
        timeout_seconds: float = 30.0,
        allowed_chat_ids: Optional[list[str]] = None,
        log_path: Optional[str] = None,
        live_trading_env: str = "LIVE_TRADING",
    ) -> None:
        live = (os.getenv(live_trading_env, "false") or "").strip().lower()
        if live != "false":
            raise LiveTradingForbidden(
                f"ApprovalGate is mock-only. Got {live_trading_env}={live!r}. Refusing to start."
            )
        self.timeout_seconds = float(timeout_seconds)
        if allowed_chat_ids is None:
            self.allowed_chat_ids = self._parse_env_allowed()
        else:
            self.allowed_chat_ids = [str(x).strip() for x in allowed_chat_ids if str(x).strip()]
        self.log_path = Path(log_path or os.getenv("APPROVALS_LOG", "logs/approvals.jsonl"))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._cond = threading.Condition()
        self._requests: dict[str, ApprovalRequest] = {}
        self._decisions: dict[str, ApprovalDecision] = {}

    @staticmethod
    def _parse_env_allowed() -> list[str]:
        raw = (os.getenv("TELEGRAM_APPROVE_CHAT_IDS", "") or "").strip()
        return [s.strip() for s in raw.split(",") if s.strip()]

    @staticmethod
    def _now() -> tuple[float, str]:
        return time.time(), datetime.now(timezone.utc).isoformat()

    def _write(self, payload: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def request(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        target: float,
        confidence: float,
        qty: float = 1.0,
        reason: str = "",
        signal_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> ApprovalRequest:
        ts, iso = self._now()
        req = ApprovalRequest(
            request_id=str(uuid.uuid4()),
            ts=ts,
            timestamp=iso,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            entry=float(entry),
            stop=float(stop),
            target=float(target),
            confidence=float(confidence),
            qty=float(qty),
            reason=reason,
            timeout_seconds=float(
                timeout_seconds if timeout_seconds is not None else self.timeout_seconds
            ),
        )
        with self._cond:
            self._requests[req.request_id] = req
        self._write(req.to_log_dict())
        return req

    def submit_decision(
        self,
        request_id: str,
        *,
        approve: bool,
        operator_chat_id: str,
    ) -> ApprovalDecision:
        wrote = False
        with self._cond:
            if request_id in self._decisions:
                return self._decisions[request_id]
            req = self._requests.get(request_id)
            if req is None:
                raise ValueError(f"unknown request_id: {request_id}")
            ts, iso = self._now()
            chat = str(operator_chat_id).strip() if operator_chat_id is not None else ""
            if not chat or chat not in self.allowed_chat_ids:
                decision = ApprovalDecision(
                    request_id=request_id,
                    approved=False,
                    reason="unauthorized_operator",
                    operator_chat_id=chat or None,
                    decided_ts=ts,
                    decided_timestamp=iso,
                    elapsed_ms=(ts - req.ts) * 1000.0,
                )
            else:
                decision = ApprovalDecision(
                    request_id=request_id,
                    approved=bool(approve),
                    reason="approved" if approve else "manual_reject",
                    operator_chat_id=chat,
                    decided_ts=ts,
                    decided_timestamp=iso,
                    elapsed_ms=(ts - req.ts) * 1000.0,
                )
            self._decisions[request_id] = decision
            self._cond.notify_all()
            wrote = True
        if wrote:
            self._write(decision.to_log_dict())
        return decision

    def await_decision(
        self,
        request: ApprovalRequest,
        *,
        timeout_seconds: Optional[float] = None,
        poll_seconds: float = 0.02,
    ) -> ApprovalDecision:
        budget = float(
            timeout_seconds if timeout_seconds is not None else request.timeout_seconds
        )
        deadline = time.monotonic() + budget
        wrote_timeout = False
        decision: Optional[ApprovalDecision] = None
        with self._cond:
            while request.request_id not in self._decisions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    ts, iso = self._now()
                    decision = ApprovalDecision(
                        request_id=request.request_id,
                        approved=False,
                        reason="approval_timeout",
                        operator_chat_id=None,
                        decided_ts=ts,
                        decided_timestamp=iso,
                        elapsed_ms=(ts - request.ts) * 1000.0,
                    )
                    self._decisions[request.request_id] = decision
                    wrote_timeout = True
                    break
                self._cond.wait(timeout=min(poll_seconds, max(remaining, 0.001)))
            if decision is None:
                decision = self._decisions[request.request_id]
        if wrote_timeout:
            self._write(decision.to_log_dict())
        return decision
