"""Telegram notifier. Optional — disabled when credentials are blank."""
from __future__ import annotations

import os
from typing import Any


class TelegramBot:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "telegram disabled"}
        import requests  # type: ignore

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        r = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=5)
        return {"ok": r.ok, "status": r.status_code}
