"""Phase 6.A-4 CLI routing tests for tools.mock_fibo_signal.

Verifies the new --route flag drives the full
RiskGate -> ApprovalGate -> ExecutorRouter pipeline, that
--approval-mode covers all four branches (auto-approve, auto-reject,
timeout, none), and that --submit / --route are mutually exclusive.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _fibo_line(y: int, conf: float = 0.8, length: int = 600) -> dict:
    return {
        "y": y,
        "x_start": 10,
        "x_end": 10 + length,
        "length": length,
        "color_bgr": [60, 255, 60],
        "color_rgb": [60, 255, 60],
        "color_hex": "#3CFF3C",
        "angle_deg": 0.0,
        "confidence": conf,
    }


def _write_fibo(fibo_path: Path, lines: list[dict]) -> None:
    fibo_path.write_text(
        json.dumps(
            {"input": "synthetic", "image_shape": [480, 800], "count": len(lines), "lines": lines}
        ),
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _run_cli(
    *,
    fibo_path: Path,
    signals_path: Path,
    extra: list[str],
    env_overrides: dict | None = None,
    price_y: float = 448.0,
) -> subprocess.CompletedProcess:
    args = [
        sys.executable, "-m", "tools.mock_fibo_signal",
        "--fibo-lines", str(fibo_path),
        "--out-signals", str(signals_path),
        "--price-y", str(price_y),
        "--symbol", "TEST",
        "--tolerance-px", "5",
        "--min-confidence", "0.55",
    ] + extra
    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
    }
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, env=env)


# ---------- mutex ----------

def test_submit_and_route_are_mutually_exclusive(tmp_path):
    fibo = tmp_path / "fibo.json"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=["--submit", "--route"],
    )
    assert r.returncode == 2
    assert "mutually exclusive" in r.stderr.lower()


# ---------- --route + EXECUTION_MODE=mock ----------

def test_route_auto_approve_mock_writes_trades_jsonl(tmp_path):
    fibo = tmp_path / "fibo.json"
    sig_log = tmp_path / "signals.jsonl"
    trades = tmp_path / "trades.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=sig_log,
        extra=[
            "--route", "--approval-mode", "auto-approve",
            "--operator-chat-id", "12345",
            "--approvals-log", str(approvals),
            "--router-log", str(router),
            "--approval-timeout", "1.0",
        ],
        env_overrides={
            "EXECUTION_MODE": "mock",
            "TRADES_LOG": str(trades),
        },
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    trade_rows = _read_jsonl(trades)
    assert len(trade_rows) == 1
    assert trade_rows[0]["mode"] == "mock"
    assert trade_rows[0]["side"] == "LONG"

    router_rows = _read_jsonl(router)
    assert any(
        r.get("submitted") is True and r.get("adapter") == "mock"
        for r in router_rows
    )


def test_route_auto_reject_does_not_submit(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "auto-reject",
            "--operator-chat-id", "12345",
            "--approvals-log", str(approvals),
            "--router-log", str(router),
            "--approval-timeout", "1.0",
        ],
        env_overrides={"TRADES_LOG": str(trades)},
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert not trades.exists() or trades.read_text(encoding="utf-8").strip() == ""
    router_rows = _read_jsonl(router)
    assert any(
        r.get("submitted") is False and r.get("skip_reason") == "approval_rejected"
        for r in router_rows
    )


def test_route_timeout_does_not_submit(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "timeout",
            "--operator-chat-id", "12345",
            "--approvals-log", str(approvals),
            "--router-log", str(router),
            "--approval-timeout", "0.1",
        ],
        env_overrides={"TRADES_LOG": str(trades)},
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert not trades.exists() or trades.read_text(encoding="utf-8").strip() == ""
    router_rows = _read_jsonl(router)
    assert any(
        r.get("skip_reason") == "approval_timeout" for r in router_rows
    )


def test_route_none_bypasses_approval_and_routes_to_mock(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "none",
            "--router-log", str(router),
        ],
        env_overrides={"TRADES_LOG": str(trades)},
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    trade_rows = _read_jsonl(trades)
    assert len(trade_rows) == 1
    assert trade_rows[0]["mode"] == "mock"
    router_rows = _read_jsonl(router)
    last = router_rows[-1]
    assert last["submitted"] is True
    assert last["adapter"] == "mock"


# ---------- --route + EXECUTION_MODE=paper ----------

def test_route_auto_approve_paper_writes_paper_trades_not_trades(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    paper_trades = tmp_path / "paper_trades.jsonl"
    router = tmp_path / "router.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    # EXECUTION_MODE env stays "mock" so RiskGate's mock-only invariant
    # is satisfied; --execution-mode paper is the explicit override that
    # tells ExecutorRouter to route to PaperBrokerAdapter.
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "auto-approve",
            "--execution-mode", "paper",
            "--operator-chat-id", "12345",
            "--approvals-log", str(approvals),
            "--router-log", str(router),
            "--approval-timeout", "1.0",
        ],
        env_overrides={
            "PAPER_TRADES_LOG": str(paper_trades),
            "TRADES_LOG": str(trades),
        },
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    assert not trades.exists() or trades.read_text(encoding="utf-8").strip() == ""

    paper_rows = _read_jsonl(paper_trades)
    assert len(paper_rows) == 1
    assert paper_rows[0]["event_type"] == "paper_open"
    assert paper_rows[0]["mode"] == "paper"

    router_rows = _read_jsonl(router)
    assert router_rows[-1]["adapter"] == "paper"
    assert router_rows[-1]["execution_mode"] == "paper"


# ---------- FLAT / risk-gate / live ----------

def test_route_with_flat_signal_does_not_enter_pipeline(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    paper_trades = tmp_path / "paper_trades.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    # price far from any line -> FLAT
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "auto-approve",
            "--router-log", str(router),
            "--approval-timeout", "1.0",
        ],
        env_overrides={"TRADES_LOG": str(trades), "PAPER_TRADES_LOG": str(paper_trades)},
        price_y=100.0,  # 350 px away from line at y=450
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert not trades.exists() or trades.read_text(encoding="utf-8").strip() == ""
    assert not paper_trades.exists()
    # router log untouched: FLAT never reached the router.
    assert not router.exists() or router.read_text(encoding="utf-8").strip() == ""


def test_route_with_low_confidence_does_not_submit(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.30)])  # below 0.55 threshold
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "auto-approve",
            "--router-log", str(router),
            "--approval-timeout", "1.0",
        ],
        env_overrides={"TRADES_LOG": str(trades)},
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    assert not trades.exists() or trades.read_text(encoding="utf-8").strip() == ""


def test_route_refuses_when_live_trading_env_flipped(tmp_path):
    """LIVE_TRADING=true must cause RiskGate to refuse at its constructor,
    so the route function returns init_failure and the CLI exits 0 (the
    signal is still written but no trade row appears)."""
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=["--route", "--approval-mode", "none"],
        env_overrides={"LIVE_TRADING": "true", "TRADES_LOG": str(trades)},
    )
    # exit code may be 0 (graceful) or non-zero depending on which gate
    # raises first; the key invariant is that no fill row exists.
    assert not trades.exists() or trades.read_text(encoding="utf-8").strip() == ""


# ---------- approvals.jsonl sanity ----------

def test_route_auto_approve_writes_approvals_jsonl_row(tmp_path):
    fibo = tmp_path / "fibo.json"
    trades = tmp_path / "trades.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    router = tmp_path / "router.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])
    r = _run_cli(
        fibo_path=fibo,
        signals_path=tmp_path / "signals.jsonl",
        extra=[
            "--route", "--approval-mode", "auto-approve",
            "--operator-chat-id", "12345",
            "--approvals-log", str(approvals),
            "--router-log", str(router),
            "--approval-timeout", "1.0",
        ],
        env_overrides={"TRADES_LOG": str(trades)},
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    rows = _read_jsonl(approvals)
    assert any(r.get("event_type") == "approval_request" for r in rows)
    assert any(
        r.get("event_type") == "approval_decision" and r.get("reason") == "approved"
        for r in rows
    )
