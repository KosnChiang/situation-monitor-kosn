"""tools.run_live_order CLI tests (Phase 7.A).

Verifies the explicitly-named live order CLI works as a thin wrapper
over tools.dry_run_live_order (same flags, same defaults).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _today_flag() -> str:
    return f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def _decision(**overrides) -> dict:
    base = {
        "decision_id": "run-live-1",
        "symbol": "TMFR1",
        "side": "LONG",
        "entry": 23010.0,
        "stop": 22995.0,
        "target": 23040.0,
        "confidence": 0.85,
        "reason": "TMF fake-live test",
        "invalidation": "below stop",
        "data_sources": ["manual"],
        "mode": "live",
    }
    base.update(overrides)
    return base


def _env_unlock(tmp_path: Path) -> dict:
    return {
        **os.environ,
        "LIVE_TRADING": "true",
        "EXECUTION_MODE": "live",
        "LIVE_READY_FLAG": _today_flag(),
        "LIVE_TOKEN_HMAC": "run-live-hmac",
        "ALLOWED_SYMBOLS": "TMFR1",
        "MAX_DAILY_LOSS": "500",
        "MAX_POSITION_SIZE": "1",
        "MIN_LIVE_CONFIDENCE": "0.7",
        "MAX_LOSS_PER_TRADE": "200",
        "MAX_DAILY_TRADES": "5",
        "FAKE_LIVE_ADAPTER": "true",
    }


def test_run_live_order_fake_default_filled(tmp_path):
    decision_path = tmp_path / "d.json"
    decision_path.write_text(json.dumps(_decision()), encoding="utf-8")
    orders = tmp_path / "live_orders.jsonl"
    fills = tmp_path / "live_fills.jsonl"
    args = [
        sys.executable, "-m", "tools.run_live_order",
        "--ai-decision", str(decision_path),
        "--qty", "1",
        "--orders-log", str(orders),
        "--fills-log", str(fills),
        "--rejections-log", str(tmp_path / "rej.jsonl"),
        "--ai-decisions-log", str(tmp_path / "ai.jsonl"),
        "--kill-file", str(tmp_path / ".killswitch"),
        "--json",
    ]
    env = _env_unlock(tmp_path)
    r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "filled"
    assert orders.exists() and fills.exists()


def test_run_live_order_is_wrapper_around_dry_run():
    """The CLI must delegate to tools.dry_run_live_order.main."""
    src = (ROOT / "tools" / "run_live_order.py").read_text(encoding="utf-8")
    assert "from tools.dry_run_live_order import main" in src
    assert "_delegate_main" in src
