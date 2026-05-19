"""tools.run_ai_swing_once CLI tests (Phase 7.A)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _alert(**overrides) -> dict:
    base = {
        "source": "tradingview", "strategy": "twtx_fibo_v4",
        "symbol": "TMFR1", "timeframe": "5", "bar_time": "t",
        "open":  23015.0, "high": 23022.0, "low": 22995.0, "close": 23018.0,
        "prev_ohlc": {"open": 23000.0, "high": 23080.0, "low": 22970.0, "close": 23055.0},
        "fibo": {
            "base": 23026.25, "range": 110.0,
            "nearest_ratio": 0.382, "nearest_price": 23068.27,
            "touch_support": True, "touch_resistance": False,
            "zone": "support",
        },
        "rsi": {
            "value": 28.5, "ma": 42.1, "state": "oversold",
            "bull_div": False, "bear_div": False,
        },
        "pivot": {"last_high": 23110.0, "last_low": 22960.0},
        "signal": {
            "buy": False, "sell": False,
            "combo_buy": True, "combo_sell": False,
            "div_buy": False, "div_sell": False,
            "touch_fibo": True,
        },
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in base:
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def _run(*, ctx_path: Path, env_overrides=None, extra=None) -> subprocess.CompletedProcess:
    args = [
        sys.executable, "-m", "tools.run_ai_swing_once",
        "--chart-context", str(ctx_path),
        "--json",
    ]
    if extra:
        args.extend(extra)
    env = {**os.environ}
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, env=env)


def test_cli_runs_and_emits_decision(tmp_path):
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps(_alert()), encoding="utf-8")
    env_overrides = {
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "CHART_CONTEXT_LOG": str(tmp_path / "chart_context.jsonl"),
        "AI_SWING_DECISIONS_LOG": str(tmp_path / "ai_swing_decisions.jsonl"),
    }
    r = _run(ctx_path=ctx, env_overrides=env_overrides)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["action"] == "ENTRY"
    assert body["side"] == "LONG"
    assert body["submitted"] is False  # --auto-submit not set


def test_cli_auto_submit_writes_live_orders(tmp_path):
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps(_alert()), encoding="utf-8")
    env_overrides = {
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "CHART_CONTEXT_LOG": str(tmp_path / "chart_context.jsonl"),
        "AI_SWING_DECISIONS_LOG": str(tmp_path / "ai_swing_decisions.jsonl"),
        "LIVE_ORDERS_LOG": str(tmp_path / "live_orders.jsonl"),
        "LIVE_FILLS_LOG": str(tmp_path / "live_fills.jsonl"),
        "LIVE_REJECTIONS_LOG": str(tmp_path / "live_rejections.jsonl"),
    }
    r = _run(ctx_path=ctx, env_overrides=env_overrides, extra=["--auto-submit"])
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["submitted"] is True
    assert body["order_id"] is not None
    assert (tmp_path / "live_orders.jsonl").exists()
    assert (tmp_path / "live_fills.jsonl").exists()


def test_cli_no_signal_no_trade(tmp_path):
    alert = _alert(
        fibo={"touch_support": False},
        rsi={"state": "neutral"},
        signal={"combo_buy": False, "div_buy": False, "touch_fibo": False},
    )
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps(alert), encoding="utf-8")
    r = _run(
        ctx_path=ctx,
        env_overrides={
            "LIVE_TRADING": "false", "EXECUTION_MODE": "mock",
            "CHART_CONTEXT_LOG": str(tmp_path / "ctx.jsonl"),
            "AI_SWING_DECISIONS_LOG": str(tmp_path / "ai.jsonl"),
        },
    )
    assert r.returncode == 0
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["action"] == "NO_TRADE"


def test_cli_invalid_json_exits_2(tmp_path):
    ctx = tmp_path / "ctx.json"
    ctx.write_text("not json", encoding="utf-8")
    r = _run(
        ctx_path=ctx,
        env_overrides={"LIVE_TRADING": "false", "EXECUTION_MODE": "mock"},
    )
    assert r.returncode == 2
