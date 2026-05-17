"""Unit + integration tests for ``tools.mock_fibo_signal``.

Covers the Phase-5 behavioural requirements:

  5. Mock signal only writes to local logs; never networks.
  6. FLAT signals are not submitted to the executor.
  7. Low-confidence touches are not submitted to the executor.

Plus structural guards: every emitted record carries the required
fields (``symbol``, ``side``, ``reason``, ``fibo_line_y``,
``confidence``, ``timestamp``, ``mode="mock"``).
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

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"
os.environ["BROKER_MODE"] = "mock"

from tools.mock_fibo_signal import generate_signal  # noqa: E402


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


# -------------------------------------------------------------- pure logic

def test_emits_long_when_price_within_tolerance():
    sig = generate_signal(
        fibo_lines=[_fibo_line(450, conf=0.8)],
        price_y=448,
        symbol="MOCK",
        tolerance_px=5,
        min_confidence=0.55,
    )
    assert sig["side"] == "LONG"
    assert sig["fibo_line_y"] == 450
    assert sig["confidence"] == 0.8
    assert sig["mode"] == "mock"


def test_emits_flat_when_no_line_within_tolerance():
    sig = generate_signal(
        fibo_lines=[_fibo_line(450, conf=0.8)],
        price_y=300,
        symbol="MOCK",
        tolerance_px=5,
        min_confidence=0.55,
    )
    assert sig["side"] == "FLAT"
    assert sig["confidence"] == 0.0


def test_emits_flat_when_confidence_too_low():
    sig = generate_signal(
        fibo_lines=[_fibo_line(450, conf=0.30)],
        price_y=450,
        symbol="MOCK",
        tolerance_px=5,
        min_confidence=0.55,
    )
    assert sig["side"] == "FLAT"
    assert "confidence" in sig["reason"].lower()


def test_emits_flat_when_no_lines_at_all():
    sig = generate_signal(
        fibo_lines=[],
        price_y=450,
        symbol="MOCK",
        tolerance_px=5,
        min_confidence=0.55,
    )
    assert sig["side"] == "FLAT"


def test_signal_record_carries_all_required_fields():
    sig = generate_signal(
        fibo_lines=[_fibo_line(450, conf=0.8)],
        price_y=448,
        symbol="XAUUSD",
        tolerance_px=5,
        min_confidence=0.55,
    )
    for field in ("ts", "timestamp", "symbol", "side", "reason",
                  "fibo_line_y", "confidence", "mode"):
        assert field in sig, f"missing field: {field}"
    assert sig["mode"] == "mock"
    assert sig["symbol"] == "XAUUSD"
    # ISO 8601 sanity
    assert "T" in sig["timestamp"]


def test_picks_nearest_line_when_multiple_within_tolerance():
    sig = generate_signal(
        fibo_lines=[_fibo_line(440, conf=0.7), _fibo_line(460, conf=0.9)],
        price_y=458,
        symbol="MOCK",
        tolerance_px=10,
        min_confidence=0.55,
    )
    assert sig["side"] == "LONG"
    assert sig["fibo_line_y"] == 460
    assert sig["confidence"] == 0.9


# -------------------------------------------------------------- CLI / IO

def _run_cli(*, fibo_path: Path, signals_path: Path, trades_path: Path,
             price_y: float, submit: bool, extra: list[str] | None = None):
    args = [
        sys.executable, "-m", "tools.mock_fibo_signal",
        "--fibo-lines", str(fibo_path),
        "--out-signals", str(signals_path),
        "--price-y", str(price_y),
        "--symbol", "TEST",
        "--tolerance-px", "5",
        "--min-confidence", "0.55",
    ]
    if submit:
        args.append("--submit")
    if extra:
        args.extend(extra)
    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
        "TRADES_LOG": str(trades_path),
    }
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, env=env)


def _write_fibo(fibo_path: Path, lines: list[dict]) -> None:
    fibo_path.write_text(
        json.dumps({"input": "synthetic", "image_shape": [480, 800], "count": len(lines), "lines": lines}),
        encoding="utf-8",
    )


def test_cli_appends_signal_to_jsonl_and_does_not_submit_by_default(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])

    r = _run_cli(fibo_path=fibo, signals_path=sig_log, trades_path=trades_log,
                 price_y=448, submit=False)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    rows = sig_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 1
    rec = json.loads(rows[0])
    assert rec["side"] == "LONG"
    assert rec["mode"] == "mock"

    # Default behavior (no --submit) must NEVER touch trades.jsonl.
    assert not trades_log.exists() or trades_log.read_text(encoding="utf-8") == ""


def test_cli_submit_routes_only_through_mock_executor(tmp_path):
    """With --submit, an approved non-FLAT signal lands in trades.jsonl,
    via MockExecutor only (no network)."""
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])

    r = _run_cli(fibo_path=fibo, signals_path=sig_log, trades_path=trades_log,
                 price_y=448, submit=True)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    assert trades_log.exists()
    fill_rows = trades_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(fill_rows) == 1
    fill = json.loads(fill_rows[0])
    assert fill["mode"] == "mock"
    assert fill["side"] == "LONG"


def test_cli_flat_signal_does_not_submit_even_with_submit_flag(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])

    # price far from any line -> FLAT
    r = _run_cli(fibo_path=fibo, signals_path=sig_log, trades_path=trades_log,
                 price_y=100, submit=True)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    rows = sig_log.read_text(encoding="utf-8").strip().splitlines()
    rec = json.loads(rows[0])
    assert rec["side"] == "FLAT"
    assert not trades_log.exists() or trades_log.read_text(encoding="utf-8") == ""


def test_cli_low_confidence_does_not_submit_even_with_submit_flag(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.30)])   # below threshold

    r = _run_cli(fibo_path=fibo, signals_path=sig_log, trades_path=trades_log,
                 price_y=450, submit=True)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    rec = json.loads(sig_log.read_text(encoding="utf-8").strip().splitlines()[0])
    assert rec["side"] == "FLAT"
    assert not trades_log.exists() or trades_log.read_text(encoding="utf-8") == ""


def test_cli_refuses_when_live_trading_is_flipped(tmp_path):
    """If LIVE_TRADING is somehow set to true at runtime, the executor
    boundary in MockExecutor refuses, so --submit cannot write a fill."""
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    _write_fibo(fibo, [_fibo_line(450, conf=0.8)])

    env = {
        **os.environ,
        "LIVE_TRADING": "true",          # the executor must refuse
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
        "TRADES_LOG": str(trades_log),
    }
    r = subprocess.run(
        [sys.executable, "-m", "tools.mock_fibo_signal",
         "--fibo-lines", str(fibo),
         "--out-signals", str(sig_log),
         "--price-y", "448",
         "--tolerance-px", "5",
         "--submit"],
        cwd=str(ROOT), capture_output=True, text=True, env=env,
    )
    # Either the RiskGate refuses at construction or MockExecutor refuses
    # at submit -- either way, no fill row may exist.
    assert not trades_log.exists() or trades_log.read_text(encoding="utf-8") == ""
