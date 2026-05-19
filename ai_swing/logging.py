"""Audit log writers for the AI swing pipeline (Phase 7.A)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from ai_swing.context import ChartContext
from ai_swing.decision import AISwingDecision


def default_chart_context_log_path() -> Path:
    return Path(os.getenv("CHART_CONTEXT_LOG", "logs/chart_context.jsonl"))


def default_ai_swing_decisions_log_path() -> Path:
    return Path(os.getenv("AI_SWING_DECISIONS_LOG", "logs/ai_swing_decisions.jsonl"))


def default_heartbeat_log_path() -> Path:
    return Path(os.getenv("AI_SWING_HEARTBEAT_LOG",
                          "logs/ai_swing_watch_heartbeat.jsonl"))


def default_webhook_log_path() -> Path:
    return Path(os.getenv("AI_SWING_WEBHOOK_LOG", "logs/ai_swing_webhook.jsonl"))


def _append(payload: dict, log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_chart_context(ctx: ChartContext, log_path: Path | None = None) -> None:
    _append(ctx.to_log_dict(), log_path or default_chart_context_log_path())


def write_ai_swing_decision(d: AISwingDecision, log_path: Path | None = None) -> None:
    _append(d.to_log_dict(), log_path or default_ai_swing_decisions_log_path())


def write_heartbeat(payload: dict, log_path: Path | None = None) -> None:
    payload = dict(payload)
    payload.setdefault("event_type", "heartbeat")
    _append(payload, log_path or default_heartbeat_log_path())


def write_webhook_event(payload: dict, log_path: Path | None = None) -> None:
    _append(payload, log_path or default_webhook_log_path())
