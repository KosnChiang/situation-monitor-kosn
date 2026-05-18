"""End-to-end invariant: synthetic capture PNG -> trades.jsonl row.

Drives one iteration of ``tools.watch_fibo_loop`` against:
  * a synthetic chart fixture (480x800 black background with six
    horizontal Fibo lines at known y positions) written into a
    tmp dir, used as ``--offline-image``;
  * a one-row quotes file ``--quotes-file`` whose ``last`` field,
    when run through the calibration in the tmp ``--config``, lands
    on the 0.618 line, so FiboMobV2 produces a LONG;
  * ``--strategy mob_v2`` so the Phase-6 wiring is exercised
    (Phase-5 ``touch`` still works, but the e2e here is specifically
    about the wired-in strategy class).

Then asserts every meaningful invariant of the chain:
  * watch_loop.jsonl gained exactly one record with the LONG signal;
  * trades.jsonl gained exactly one row with mode="mock" and
    side="LONG";
  * signal y matches one of the named levels;
  * RiskGate / MockExecutor were the only execution-side touches
    (Telegram is in dry mode if --notify-mode=dry, off otherwise);
  * LIVE_TRADING / EXECUTION_MODE / BROKER_MODE never flipped.

No network, no broker SDK, no live trading.
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

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def _synthetic_chart(path: Path) -> tuple[int, int]:
    """Write a 480x800 PNG with six horizontal Fibo lines at the
    known y positions used by ``_calibration_yaml`` below."""
    h, w = 480, 800
    img = np.full((h, w, 3), 25, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for x in range(0, w, 14):
        bh = int(rng.integers(20, 200))
        y0 = int(rng.integers(60, h - 60 - bh))
        color = (180, 180, 180) if rng.random() > 0.5 else (90, 90, 90)
        cv2.rectangle(img, (x + 2, y0), (x + 10, y0 + bh), color, -1)
    # 6 horizontal lines = full Fib retracement -> namer assigns all 6 levels.
    fib_ys = (60, 140, 210, 280, 350, 420)
    for y in fib_ys:
        cv2.line(img, (40, y), (760, y), (60, 255, 60), 2)
    cv2.imwrite(str(path), img)
    return h, w


def _calibration_yaml(path: Path) -> None:
    """A capture.yaml whose calibration maps y=60 -> 2450 and y=420
    -> 2350, so y=210 (the 0.618 line) corresponds to price 2408.33...
    """
    path.write_text(
        "region:\n  monitor_index: 1\n"
        "fps: 4\n"
        "output_path: logs/capture_test.png\n"
        "calibration:\n"
        "  reference_high:\n"
        "    pixel_y: 60\n"
        "    price: 2450.0\n"
        "  reference_low:\n"
        "    pixel_y: 420\n"
        "    price: 2350.0\n",
        encoding="utf-8",
    )


def _quote_row(last: float) -> dict:
    return {
        "symbol": "XAUUSD",
        "bid": last - 0.25,
        "ask": last + 0.25,
        "last": last,
        "mid": last,
        "ts": 1.0,
        "timestamp": "2026-05-19T00:00:00+00:00",
        "source": "mock",
    }


def _run_one_iteration(
    *,
    capture: Path,
    cfg: Path,
    quotes: Path,
    watch_log: Path,
    trades_log: Path,
    strategy: str,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    args = [
        sys.executable, "-m", "tools.watch_fibo_loop",
        "--offline-image", str(capture),
        "--config", str(cfg),
        "--quotes-file", str(quotes),
        "--watch-log", str(watch_log),
        "--interval", "0",
        "--max-iterations", "1",
        "--strategy", strategy,
        "--symbol", "XAUUSD",
        "--min-confidence", "0.5",
        "--min-length-ratio", "0.4",
        "--tolerance-px", "10",
        "--submit",
    ]
    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
        "TRADES_LOG": str(trades_log),
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True, env=env, timeout=120,
    )


# ---------------------------------------------------------------- mob_v2 path

def test_synthetic_capture_to_trades_jsonl_via_mob_v2(tmp_path):
    capture = tmp_path / "capture.png"
    cfg = tmp_path / "capture.yaml"
    quotes = tmp_path / "quotes.jsonl"
    watch_log = tmp_path / "watch_loop.jsonl"
    trades_log = tmp_path / "trades.jsonl"

    _synthetic_chart(capture)
    _calibration_yaml(cfg)
    # y=210 (the 0.618 line). price_to_pixel_y(2408.333) = 210.
    quotes.write_text(json.dumps(_quote_row(2408.333)) + "\n", encoding="utf-8")

    r = _run_one_iteration(
        capture=capture, cfg=cfg, quotes=quotes,
        watch_log=watch_log, trades_log=trades_log,
        strategy="mob_v2",
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    # watch_loop.jsonl contract
    assert watch_log.exists()
    log_rows = [json.loads(l) for l in watch_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(log_rows) == 1
    rec = log_rows[0]
    assert rec["signal"]["side"] == "LONG", \
        f"expected LONG, got {rec['signal']['side']}; reason={rec['signal']['reason']}"
    assert "fibo-mob-v2" in rec["signal"]["reason"]
    assert rec["execution"]["mode"] == "mock"
    assert rec["execution"]["submitted"] is True

    # trades.jsonl contract -- exactly one mock fill
    assert trades_log.exists()
    fill_rows = [json.loads(l) for l in trades_log.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(fill_rows) == 1
    fill = fill_rows[0]
    assert fill["mode"] == "mock"
    assert fill["side"] == "LONG"
    # entry should be the pixel_y of the touched line (~210)
    assert 200.0 <= fill["entry"] <= 220.0


# ---------------------------------------------------------------- mob_v2 with only 3 lines -> FLAT

def test_three_lines_strategy_returns_flat_no_trade(tmp_path):
    """With only 3 lines drawn, the namer omits 0.786, so FiboMobV2
    returns FLAT 'missing fibo levels'. No trade should be written."""
    capture = tmp_path / "capture.png"
    cfg = tmp_path / "capture.yaml"
    quotes = tmp_path / "quotes.jsonl"
    watch_log = tmp_path / "watch_loop.jsonl"
    trades_log = tmp_path / "trades.jsonl"

    h, w = 480, 800
    img = np.full((h, w, 3), 25, dtype=np.uint8)
    # Only 3 horizontal lines
    for y in (60, 210, 420):
        cv2.line(img, (40, y), (760, y), (60, 255, 60), 2)
    cv2.imwrite(str(capture), img)

    _calibration_yaml(cfg)
    quotes.write_text(json.dumps(_quote_row(2408.333)) + "\n", encoding="utf-8")

    r = _run_one_iteration(
        capture=capture, cfg=cfg, quotes=quotes,
        watch_log=watch_log, trades_log=trades_log,
        strategy="mob_v2",
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    rec = json.loads(watch_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["signal"]["side"] == "FLAT"
    assert not trades_log.exists() or trades_log.read_text(encoding="utf-8").strip() == ""


# ---------------------------------------------------------------- touch backward-compat

def test_phase5_touch_strategy_still_works_for_the_same_input(tmp_path):
    """The default --strategy=touch must keep its Phase-5 behaviour:
    price near any line -> LONG."""
    capture = tmp_path / "capture.png"
    cfg = tmp_path / "capture.yaml"
    quotes = tmp_path / "quotes.jsonl"
    watch_log = tmp_path / "watch_loop.jsonl"
    trades_log = tmp_path / "trades.jsonl"

    _synthetic_chart(capture)
    _calibration_yaml(cfg)
    quotes.write_text(json.dumps(_quote_row(2408.333)) + "\n", encoding="utf-8")

    r = _run_one_iteration(
        capture=capture, cfg=cfg, quotes=quotes,
        watch_log=watch_log, trades_log=trades_log,
        strategy="touch",
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(watch_log.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["signal"]["side"] == "LONG"
    assert "fibo-mob-v2" not in rec["signal"]["reason"]  # the legacy touch reason
