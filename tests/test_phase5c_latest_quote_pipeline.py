"""End-to-end Phase 5.C tests:

    filtered Fibo line  +  latest_quote  +  chart calibration
       ->  pixel_y match  ->  LONG  ->  RiskGate  ->  MockExecutor

Glues together the real Phase-5.B fixture (the actual 18-line
TradingView capture, filtered down to 1 Material-green line at
y=1533) with a synthetic chart calibration designed so a clean
round price ($2250.0) maps exactly onto that line. Runs the existing
``tools.filter_fibo_lines`` and ``tools.mock_fibo_signal`` CLIs in
subprocesses with the mock-only envelope pinned, then asserts the
signals.jsonl + trades.jsonl rows that result.

No production source file is modified. No new CLI flag is added. The
default ``mock_fibo_signal --fibo-lines`` is unchanged
(``logs/fibo_lines.json``); tests pass the filtered path explicitly.

Constraints re-asserted at every subprocess call:

  * LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock
  * TRADES_LOG redirected into tmp_path so the test never touches
    the real logs/trades.jsonl
  * No broker SDK / network library is imported (covered by
    test_no_live_trading_phase5c.py).
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

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")
os.environ.setdefault("BROKER_MODE", "mock")

from vision.chart_calibration import ChartCalibration  # noqa: E402

FIXTURE_FIBO_18  = ROOT / "tests" / "fixtures" / "fibo_lines_18.json"
FIXTURE_CAL_DEMO = ROOT / "tests" / "fixtures" / "capture_calibration_demo.yaml"

# Anchors derived from the demo calibration; tested separately in C4.
DEMO_FIBO_Y     = 1533
DEMO_PRICE_HIT  = 2250.0   # maps to pixel_y = 1533
DEMO_PRICE_MISS = 2400.0   # maps to pixel_y = 33, well away from any kept line


def _mock_env(tmp_path: Path) -> dict:
    """Subprocess env that pins the mock envelope and isolates trades.jsonl."""
    env = os.environ.copy()
    env["PYTHONPATH"]     = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LIVE_TRADING"]   = "false"
    env["EXECUTION_MODE"] = "mock"
    env["BROKER_MODE"]    = "mock"
    env["TRADES_LOG"]     = str(tmp_path / "trades.jsonl")
    return env


def _write_quote(path: Path, last: float, *, symbol: str = "XAUUSD") -> None:
    rec = {
        "symbol": symbol,
        "bid": last - 0.25,
        "ask": last + 0.25,
        "last": last,
        "mid": last,
        "ts": 1.0,
        "timestamp": "2026-05-18T00:00:00+00:00",
        "source": "mock",
    }
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def _run_filter(in_path: Path, out_path: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.filter_fibo_lines",
         "--in", str(in_path), "--out", str(out_path)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )


def _run_signal(
    *,
    fibo: Path,
    quotes: Path,
    signals: Path,
    calibration: Path | None,
    submit: bool,
    env: dict,
    tolerance_px: int = 8,
    min_confidence: float = 0.55,
) -> subprocess.CompletedProcess:
    args = [
        sys.executable, "-m", "tools.mock_fibo_signal",
        "--fibo-lines",   str(fibo),
        "--out-signals",  str(signals),
        "--price-source", "latest_quote",
        "--quotes-file",  str(quotes),
        "--symbol",       "XAUUSD",
        "--tolerance-px", str(tolerance_px),
        "--min-confidence", str(min_confidence),
    ]
    if calibration is not None:
        args.extend(["--calibration-config", str(calibration)])
    if submit:
        args.append("--submit")
    return subprocess.run(args, cwd=str(ROOT), env=env, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# C1: happy path -- filter + calibrated quote that lands on the Fibo line
# ---------------------------------------------------------------------------

def test_filtered_to_signal_long_via_calibration_and_writes_mock_trade(tmp_path):
    env = _mock_env(tmp_path)
    raw      = tmp_path / "fibo_lines.json"
    filtered = tmp_path / "fibo_lines_filtered.json"
    quotes   = tmp_path / "quotes.jsonl"
    signals  = tmp_path / "signals.jsonl"

    raw.write_text(FIXTURE_FIBO_18.read_text(encoding="utf-8"), encoding="utf-8")
    _write_quote(quotes, DEMO_PRICE_HIT)

    r1 = _run_filter(raw, filtered, env)
    assert r1.returncode == 0, f"filter failed: {r1.stderr}"
    payload = json.loads(filtered.read_text(encoding="utf-8"))
    assert payload["kept_count"] == 1 and payload["lines"][0]["y"] == DEMO_FIBO_Y

    r2 = _run_signal(fibo=filtered, quotes=quotes, signals=signals,
                     calibration=FIXTURE_CAL_DEMO, submit=True, env=env)
    assert r2.returncode == 0, f"signal failed: {r2.stderr}\n{r2.stdout}"

    sig = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert sig["side"] == "LONG"
    assert sig["fibo_line_y"] == DEMO_FIBO_Y
    assert sig["mode"] == "mock"
    assert sig["confidence"] == pytest.approx(0.896, abs=0.01)

    trades_path = Path(env["TRADES_LOG"])
    assert trades_path.exists(), "MockExecutor did not write trades.jsonl"
    trade = json.loads(trades_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert trade["mode"] == "mock"
    assert trade["side"] == "LONG"
    assert trade["entry"] == pytest.approx(float(DEMO_FIBO_Y), abs=0.001)


# ---------------------------------------------------------------------------
# C2: quote price far from the Fibo line -> FLAT, no trade written
# ---------------------------------------------------------------------------

def test_filtered_quote_far_from_line_is_flat_and_no_trade(tmp_path):
    env = _mock_env(tmp_path)
    raw      = tmp_path / "fibo_lines.json"
    filtered = tmp_path / "fibo_lines_filtered.json"
    quotes   = tmp_path / "quotes.jsonl"
    signals  = tmp_path / "signals.jsonl"

    raw.write_text(FIXTURE_FIBO_18.read_text(encoding="utf-8"), encoding="utf-8")
    _write_quote(quotes, DEMO_PRICE_MISS)  # maps to pixel_y=33, far from 1533

    r1 = _run_filter(raw, filtered, env)
    assert r1.returncode == 0, r1.stderr

    r2 = _run_signal(fibo=filtered, quotes=quotes, signals=signals,
                     calibration=FIXTURE_CAL_DEMO, submit=True, env=env)
    assert r2.returncode == 0, f"{r2.stderr}\n{r2.stdout}"

    sig = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert sig["side"] == "FLAT"
    assert "no fibo line within" in sig["reason"]
    assert not Path(env["TRADES_LOG"]).exists(), "no trade should be written on FLAT"


# ---------------------------------------------------------------------------
# C3: missing calibration block -> fallback to raw price-as-pixel-y
# ---------------------------------------------------------------------------

def test_filtered_without_calibration_falls_back_and_is_flat_on_real_capture_scale(tmp_path):
    env = _mock_env(tmp_path)
    raw      = tmp_path / "fibo_lines.json"
    filtered = tmp_path / "fibo_lines_filtered.json"
    quotes   = tmp_path / "quotes.jsonl"
    signals  = tmp_path / "signals.jsonl"
    cal_cfg  = tmp_path / "capture_no_calibration.yaml"
    cal_cfg.write_text("region:\n  monitor_index: 2\n", encoding="utf-8")

    raw.write_text(FIXTURE_FIBO_18.read_text(encoding="utf-8"), encoding="utf-8")
    _write_quote(quotes, DEMO_PRICE_HIT)  # raw fallback treats 2250.0 as pixel_y

    r1 = _run_filter(raw, filtered, env)
    assert r1.returncode == 0, r1.stderr

    r2 = _run_signal(fibo=filtered, quotes=quotes, signals=signals,
                     calibration=cal_cfg, submit=True, env=env)
    assert r2.returncode == 0, f"{r2.stderr}\n{r2.stdout}"

    sig = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert sig["side"] == "FLAT"
    # 2250 (raw) is ~717 px away from the kept Fibo line at y=1533; well outside tolerance.
    assert not Path(env["TRADES_LOG"]).exists()


# ---------------------------------------------------------------------------
# C4: calibration is a self-inverse (pixel_y -> price -> pixel_y)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pixel_y", [33, 540, 1080, 1533, 2033])
def test_demo_calibration_is_roundtrip_identity(pixel_y):
    cal = ChartCalibration.from_yaml(FIXTURE_CAL_DEMO)
    assert cal is not None
    price = cal.pixel_y_to_price(pixel_y)
    back  = cal.price_to_pixel_y(price)
    assert back == pytest.approx(pixel_y, abs=1e-6)


def test_demo_calibration_anchor_maps_fibo_y_to_round_price():
    """The fixture is hand-picked so y=1533 maps to a clean $2250.00.
    Drift in the fixture should be a loud test failure."""
    cal = ChartCalibration.from_yaml(FIXTURE_CAL_DEMO)
    assert cal is not None
    assert cal.pixel_y_to_price(DEMO_FIBO_Y) == pytest.approx(DEMO_PRICE_HIT, abs=1e-6)
    assert cal.price_to_pixel_y(DEMO_PRICE_HIT) == pytest.approx(DEMO_FIBO_Y, abs=1e-6)


# ---------------------------------------------------------------------------
# C5: empty quotes.jsonl -> FLAT with "no price" reason, no crash
# ---------------------------------------------------------------------------

def test_filtered_with_empty_quotes_file_emits_flat_no_price(tmp_path):
    env = _mock_env(tmp_path)
    raw      = tmp_path / "fibo_lines.json"
    filtered = tmp_path / "fibo_lines_filtered.json"
    quotes   = tmp_path / "quotes.jsonl"
    signals  = tmp_path / "signals.jsonl"

    raw.write_text(FIXTURE_FIBO_18.read_text(encoding="utf-8"), encoding="utf-8")
    quotes.write_text("", encoding="utf-8")

    r1 = _run_filter(raw, filtered, env)
    assert r1.returncode == 0, r1.stderr

    r2 = _run_signal(fibo=filtered, quotes=quotes, signals=signals,
                     calibration=FIXTURE_CAL_DEMO, submit=True, env=env)
    assert r2.returncode == 0, f"{r2.stderr}\n{r2.stdout}"

    sig = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert sig["side"] == "FLAT"
    assert "no price available" in sig["reason"]
    assert not Path(env["TRADES_LOG"]).exists()
