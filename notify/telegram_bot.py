"""Telegram notifier.

Three behaviours, in this order of precedence:

1. **Dry-run** (``TELEGRAM_DRY_RUN=1`` or ``dry_run=True`` constructor
   arg): never networks. ``send()`` returns a record with
   ``dry_run=True`` and prints what would have been sent.
2. **Disabled** (no ``TELEGRAM_BOT_TOKEN`` *or* no ``TELEGRAM_CHAT_ID``):
   never networks. ``send()`` returns ``{"ok": False, "skipped": True}``
   without raising. This is the default on a fresh checkout so an
   integration that wires Telegram into the signal loop cannot
   accidentally break the loop just because credentials are missing.
3. **Live** (both env vars set, dry-run off): posts to the Telegram
   Bot API via ``requests``. This module reads only Telegram-specific
   env names; it never reads any broker credential.
"""
from __future__ import annotations

import os
from typing import Any


_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    return (os.getenv(name, "") or "").strip().lower() in _TRUTHY


class TelegramBot:
    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        dry_run: bool | None = None,
    ) -> None:
        self.token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        self.dry_run = dry_run if dry_run is not None else _env_truthy("TELEGRAM_DRY_RUN")

    @property
    def configured(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    @property
    def enabled(self) -> bool:
        return self.configured and not self.dry_run

    def send(self, text: str) -> dict[str, Any]:
        if self.dry_run:
            print(f"[telegram dry-run] would send to chat_id={self.chat_id or '<unset>'}: {text}")
            return {
                "ok": True,
                "dry_run": True,
                "skipped": False,
                "text": text,
                "chat_id": self.chat_id or None,
            }
        if not self.configured:
            return {
                "ok": False,
                "dry_run": False,
                "skipped": True,
                "reason": "telegram disabled (missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID)",
            }
        import requests  # type: ignore

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        r = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=5)
        return {"ok": r.ok, "dry_run": False, "skipped": False, "status": r.status_code}
