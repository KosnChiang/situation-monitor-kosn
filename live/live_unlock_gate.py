"""LiveUnlockGate (Phase 6.B-1).

Final gate before any code is allowed to construct a live-mode
``ExecutorRouter``. Verifies every required env + filesystem
precondition is in place and emits an opaque ``LiveUnlockToken`` that
the router validates.

Required preconditions (each one is independent; ANY missing =>
refuse):

  * LIVE_TRADING=true
  * EXECUTION_MODE=live
  * LIVE_READY_FLAG=approved-YYYYMMDD (today, UTC)
  * LIVE_TOKEN_HMAC non-empty
  * ALLOWED_SYMBOLS non-empty (comma-separated)
  * MAX_DAILY_LOSS non-empty
  * MAX_POSITION_SIZE non-empty
  * LIVE_BROKER_ADAPTER_PATH non-empty  OR  FAKE_LIVE_ADAPTER=true
  * logs/.killswitch must NOT exist

The token includes only an HMAC fingerprint ("***" + last 4 chars),
never the raw value. The token has a short lifetime (default 300s)
so a leaked token cannot be reused indefinitely.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_TOKEN_LIFETIME_SECONDS = 300.0
TRUTHY = {"1", "true", "yes", "on"}


class LiveUnlockForbidden(RuntimeError):
    """Raised when LiveUnlockGate cannot unlock the live path."""


@dataclass(frozen=True)
class LiveUnlockToken:
    granted_ts: float
    valid_until_ts: float
    ready_flag: str
    hmac_fingerprint: str

    def is_expired(self) -> bool:
        return time.time() > self.valid_until_ts


class LiveUnlockGate:
    def __init__(
        self,
        *,
        kill_file_path: str | Path = "logs/.killswitch",
        token_lifetime_seconds: float = DEFAULT_TOKEN_LIFETIME_SECONDS,
    ) -> None:
        self.kill_file_path = Path(kill_file_path)
        self.token_lifetime_seconds = float(token_lifetime_seconds)

    @staticmethod
    def today_flag() -> str:
        return f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"

    def check_conditions(self) -> tuple[bool, list[str]]:
        missing: list[str] = []

        live = (os.getenv("LIVE_TRADING", "false") or "").strip().lower()
        if live != "true":
            missing.append(f"LIVE_TRADING_not_true:got={live!r}")

        mode = (os.getenv("EXECUTION_MODE", "mock") or "").strip().lower()
        if mode != "live":
            missing.append(f"EXECUTION_MODE_not_live:got={mode!r}")

        ready = (os.getenv("LIVE_READY_FLAG", "") or "").strip()
        today = self.today_flag()
        if ready != today:
            missing.append(f"LIVE_READY_FLAG_mismatch:got={ready!r}_expected={today!r}")

        token_hmac = (os.getenv("LIVE_TOKEN_HMAC", "") or "").strip()
        if not token_hmac:
            missing.append("LIVE_TOKEN_HMAC_empty")

        allowed = (os.getenv("ALLOWED_SYMBOLS", "") or "").strip()
        if not allowed:
            missing.append("ALLOWED_SYMBOLS_empty")

        max_daily_loss = (os.getenv("MAX_DAILY_LOSS", "") or "").strip()
        if not max_daily_loss:
            missing.append("MAX_DAILY_LOSS_empty")

        max_position_size = (os.getenv("MAX_POSITION_SIZE", "") or "").strip()
        if not max_position_size:
            missing.append("MAX_POSITION_SIZE_empty")

        adapter_path = (os.getenv("LIVE_BROKER_ADAPTER_PATH", "") or "").strip()
        fake_adapter = (os.getenv("FAKE_LIVE_ADAPTER", "") or "").strip().lower() in TRUTHY
        if not adapter_path and not fake_adapter:
            missing.append(
                "no_adapter_specified:LIVE_BROKER_ADAPTER_PATH_empty_and_FAKE_LIVE_ADAPTER_not_truthy"
            )

        if self.kill_file_path.exists():
            missing.append(f"killswitch_file_present:{self.kill_file_path}")

        return (not missing), missing

    def unlock(self) -> LiveUnlockToken:
        ok, missing = self.check_conditions()
        if not ok:
            raise LiveUnlockForbidden(
                "LiveUnlockGate refuses to unlock; missing: " + ", ".join(missing)
            )
        granted = time.time()
        hmac_raw = os.getenv("LIVE_TOKEN_HMAC", "") or ""
        fingerprint = ("***" + hmac_raw[-4:]) if len(hmac_raw) >= 4 else "***"
        return LiveUnlockToken(
            granted_ts=granted,
            valid_until_ts=granted + self.token_lifetime_seconds,
            ready_flag=self.today_flag(),
            hmac_fingerprint=fingerprint,
        )
