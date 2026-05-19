"""KillSwitch (Phase 6.B-1).

Three independent activation sources, any of which trips the switch:

  1. File-based: ``logs/.killswitch`` exists (set by an operator
     touching the file, or by an external watchdog).
  2. Env-based: ``LIVE_KILL_SWITCH`` is "1" / "true" / "yes" / "on".
  3. Session-based: ``KillSwitch.trip(reason)`` called from in-process
     code -- the daily-loss and consecutive-loss auto-trip paths use
     this.

v1 explicitly does NOT auto-close existing positions. It only stops
new orders. Auto-flatten is operator-side work.
"""
from __future__ import annotations

import os
from pathlib import Path


TRUTHY = {"1", "true", "yes", "on"}


class KillSwitch:
    def __init__(
        self,
        *,
        kill_file_path: str | Path = "logs/.killswitch",
        max_daily_loss: float = 100.0,
        max_consecutive_losses: int = 3,
    ) -> None:
        self.kill_file_path = Path(kill_file_path)
        self.max_daily_loss = float(max_daily_loss)
        self.max_consecutive_losses = int(max_consecutive_losses)
        self._session_tripped: bool = False
        self._session_reasons: list[str] = []
        self._daily_loss: float = 0.0
        self._consecutive_losses: int = 0

    @property
    def daily_loss(self) -> float:
        return self._daily_loss

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses

    def trip(self, reason: str) -> None:
        self._session_tripped = True
        self._session_reasons.append(reason)

    def is_active(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        if self.kill_file_path.exists():
            reasons.append(f"kill_file_present:{self.kill_file_path}")
        env_val = (os.getenv("LIVE_KILL_SWITCH", "") or "").strip().lower()
        if env_val in TRUTHY:
            reasons.append(f"env_kill_switch:LIVE_KILL_SWITCH={env_val}")
        if self._session_tripped:
            for r in self._session_reasons:
                reasons.append(f"session:{r}")
        return bool(reasons), reasons

    def record_loss(self, loss: float) -> None:
        if loss <= 0:
            return
        self._daily_loss += loss
        self._consecutive_losses += 1
        if self._daily_loss >= self.max_daily_loss:
            self.trip(f"daily_loss_exceeded:{self._daily_loss:.2f}>={self.max_daily_loss:.2f}")
        if self._consecutive_losses >= self.max_consecutive_losses:
            self.trip(
                f"consecutive_losses:{self._consecutive_losses}>={self.max_consecutive_losses}"
            )

    def record_win(self, profit: float) -> None:
        if profit <= 0:
            return
        self._consecutive_losses = 0
