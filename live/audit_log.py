"""Live audit log helpers (Phase 6.B-1).

Three append-only JSONL files keep the live order lifecycle:
  * logs/live_orders.jsonl
  * logs/live_fills.jsonl
  * logs/live_rejections.jsonl

Env overrides:
  LIVE_ORDERS_LOG, LIVE_FILLS_LOG, LIVE_REJECTIONS_LOG

All records carry ``mode="live"`` so an operator can grep across all
logs to find the full live trace.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from live.models import LiveFill, LiveOrder, LiveRejection


def default_order_log_path() -> Path:
    return Path(os.getenv("LIVE_ORDERS_LOG", "logs/live_orders.jsonl"))


def default_fill_log_path() -> Path:
    return Path(os.getenv("LIVE_FILLS_LOG", "logs/live_fills.jsonl"))


def default_rejection_log_path() -> Path:
    return Path(os.getenv("LIVE_REJECTIONS_LOG", "logs/live_rejections.jsonl"))


def _append(payload: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_live_order(order: LiveOrder, log_path: Path | None = None) -> None:
    _append(order.to_log_dict(), log_path or default_order_log_path())


def write_live_fill(fill: LiveFill, log_path: Path | None = None) -> None:
    _append(fill.to_log_dict(), log_path or default_fill_log_path())


def write_live_rejection(rejection: LiveRejection, log_path: Path | None = None) -> None:
    _append(rejection.to_log_dict(), log_path or default_rejection_log_path())
