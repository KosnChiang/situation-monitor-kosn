"""CLI subprocess tests for tools.dry_run_live_order (Phase 6.B-2)."""
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


def _decision_dict(**overrides) -> dict:
    base = {
        "decision_id": "cli-test-1",
        "symbol": "XAUUSD",
        "side": "LONG",
        "entry": 23010.5,
        "stop": 22995.0,
        "target": 23040.0,
        "confidence": 0.85,
        "reason": "fibo MOB",
        "invalidation": "below stop",
        "data_sources": ["fibo_lines_filtered.json"],
        "mode": "live",
    }
    base.update(overrides)
    return base


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _unlock_env() -> dict:
    return {
        "LIVE_TRADING": "true",
        "EXECUTION_MODE": "live",
        "LIVE_READY_FLAG": _today_flag(),
        "LIVE_TOKEN_HMAC": "cli-test-hmac",
        "ALLOWED_SYMBOLS": "XAUUSD,EURUSD",
        "MAX_DAILY_LOSS": "50",
        "MAX_POSITION_SIZE": "1",
        "MIN_LIVE_CONFIDENCE": "0.7",
        "MAX_LOSS_PER_TRADE": "100",
        "MAX_DAILY_TRADES": "5",
        "FAKE_LIVE_ADAPTER": "true",
    }


def _run_cli(
    *,
    tmp_path: Path,
    decision: dict | str,
    qty: float = 1.0,
    extra: list[str] | None = None,
    env_overrides: dict | None = None,
    use_stdin: bool = False,
    daily_trade_count: int = 0,
    daily_loss: float = 0.0,
) -> subprocess.CompletedProcess:
    decision_path = tmp_path / "decision.json"
    if isinstance(decision, str):
        decision_path.write_text(decision, encoding="utf-8")
    else:
        decision_path.write_text(json.dumps(decision), encoding="utf-8")

    orders = tmp_path / "live_orders.jsonl"
    fills = tmp_path / "live_fills.jsonl"
    rejections = tmp_path / "live_rejections.jsonl"
    ai_dec = tmp_path / "ai_decisions.jsonl"
    kill = tmp_path / ".killswitch"

    args = [
        sys.executable, "-m", "tools.dry_run_live_order",
        "--ai-decision", "-" if use_stdin else str(decision_path),
        "--qty", str(qty),
        "--daily-trade-count", str(daily_trade_count),
        "--daily-loss", str(daily_loss),
        "--orders-log", str(orders),
        "--fills-log", str(fills),
        "--rejections-log", str(rejections),
        "--ai-decisions-log", str(ai_dec),
        "--kill-file", str(kill),
        "--json",
    ]
    if extra:
        args.extend(extra)
    env = {**os.environ, **_unlock_env()}
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v

    stdin_input = None
    if use_stdin:
        stdin_input = decision_path.read_text(encoding="utf-8")

    return subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True, env=env,
        input=stdin_input,
    )


# ---------- exit 0 ----------

def test_cli_happy_path_exit_0(tmp_path):
    r = _run_cli(tmp_path=tmp_path, decision=_decision_dict())
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "filled"
    assert body["exit_code"] == 0
    assert body["fill_price"] == 23010.5

    orders = _read_jsonl(tmp_path / "live_orders.jsonl")
    fills = _read_jsonl(tmp_path / "live_fills.jsonl")
    assert orders and fills
    assert orders[0]["order_id"] == fills[0]["order_id"] == body["order_id"]


def test_cli_stdin_input(tmp_path):
    r = _run_cli(tmp_path=tmp_path, decision=_decision_dict(), use_stdin=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout.strip().splitlines()[-1])["outcome"] == "filled"


# ---------- exit 2: invalid ai_decision ----------

def test_cli_invalid_json_exit_2(tmp_path):
    r = _run_cli(tmp_path=tmp_path, decision="not valid json at all")
    assert r.returncode == 2
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "ai_invalid"


def test_cli_missing_file_exit_2(tmp_path):
    args = [
        sys.executable, "-m", "tools.dry_run_live_order",
        "--ai-decision", str(tmp_path / "does_not_exist.json"),
        "--qty", "1",
        "--json",
    ]
    r = subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True,
        env={**os.environ, **_unlock_env()},
    )
    assert r.returncode == 2
    assert "not found" in r.stderr.lower()


# ---------- exit 3: unlock refused ----------

def test_cli_unlock_refused_exit_3(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path, decision=_decision_dict(),
        env_overrides={"MAX_DAILY_LOSS": None},
    )
    assert r.returncode == 3
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "unlock_refused"
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r["rejection_layer"] == "live_unlock_gate" for r in rejections)


# ---------- exit 4: kill switch ----------

def test_cli_kill_switch_env_exit_4(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path, decision=_decision_dict(),
        env_overrides={"LIVE_KILL_SWITCH": "1"},
    )
    assert r.returncode == 4
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "killed"


# ---------- exit 5: micro rejected ----------

def test_cli_oversize_qty_exit_5(tmp_path):
    r = _run_cli(tmp_path=tmp_path, decision=_decision_dict(), qty=99.0)
    assert r.returncode == 5
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "micro_rejected"


def test_cli_symbol_not_allowed_exit_5(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(symbol="BTCUSD"),
    )
    assert r.returncode == 5


# ---------- exit 6: adapter error ----------

# Not directly testable from the CLI without injecting a broken
# adapter; covered in test_live_pipeline.py at the unit level.


# ---------- exit 7: not tradable ----------

def test_cli_flat_side_exit_7(tmp_path):
    r = _run_cli(tmp_path=tmp_path, decision=_decision_dict(side="FLAT"))
    assert r.returncode == 7
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "not_tradable"


def test_cli_watch_side_exit_7(tmp_path):
    r = _run_cli(tmp_path=tmp_path, decision=_decision_dict(side="WATCH"))
    assert r.returncode == 7


def test_cli_low_confidence_exit_7(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(confidence=0.3),
        extra=["--min-trade-confidence", "0.7"],
    )
    assert r.returncode == 7
