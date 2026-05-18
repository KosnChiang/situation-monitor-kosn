"""End-to-end tests for ``tools.mock_fibo_signal`` + chart calibration.

Verifies that ``--price-source latest_quote`` correctly applies the
``calibration:`` block in ``--calibration-config`` to map a real
price into pixel-y before comparing against the detected Fibo lines.

Three scenarios:

  * calibration present and the converted pixel-y lands on a Fibo line
    -> LONG (the wired path);
  * calibration present but converted pixel-y is far from every line
    -> FLAT;
  * calibration block absent -> the generator falls back to raw
    price-as-pixel-y, matching the Phase-5 unit-test invariant.

Also:

  * a malformed calibration block must surface as a FLAT signal whose
    reason cites the calibration error (the generator never crashes
    on a bad config);
  * `tools.calibrate_chart` validates its inputs and refuses to print
    a YAML block for an inverted axis.
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


def _write_calibration(path: Path) -> None:
    # pixel_y 60 -> 2450, pixel_y 420 -> 2350
    # slope -100 / 360 = -0.2777..; price 2400 -> pixel_y 240
    path.write_text(
        "calibration:\n"
        "  reference_high:\n    pixel_y: 60\n    price: 2450.0\n"
        "  reference_low:\n    pixel_y: 420\n    price: 2350.0\n",
        encoding="utf-8",
    )


def _write_fibo(path: Path, lines: list[dict]) -> None:
    path.write_text(
        json.dumps({"input": "synthetic", "image_shape": [480, 800],
                    "count": len(lines), "lines": lines}),
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
        "timestamp": "2026-05-17T00:00:00+00:00",
        "source": "mock",
    }


def _line(y: int, *, conf: float = 0.9, length: int = 690) -> dict:
    return {
        "y": y, "x_start": 10, "x_end": 10 + length, "length": length,
        "color_bgr": [60, 255, 60], "color_rgb": [60, 255, 60],
        "color_hex": "#3CFF3C", "angle_deg": 0.0, "confidence": conf,
    }


def _run(*, fibo: Path, quotes: Path, signals: Path,
         calibration_config: Path | None,
         tolerance_px: int = 5,
         extra_env: dict | None = None):
    args = [
        sys.executable, "-m", "tools.mock_fibo_signal",
        "--fibo-lines", str(fibo),
        "--out-signals", str(signals),
        "--price-source", "latest_quote",
        "--quotes-file", str(quotes),
        "--symbol", "XAUUSD",
        "--tolerance-px", str(tolerance_px),
        "--min-confidence", "0.55",
    ]
    if calibration_config is not None:
        args.extend(["--calibration-config", str(calibration_config)])
    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
    }
    if extra_env:
        env.update(extra_env)
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, env=env)


# ----------------------------------------------------------------- happy paths

def test_calibration_maps_quote_price_to_pixel_y_and_fires_long(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    quotes = tmp_path / "quotes.jsonl"
    signals = tmp_path / "signals.jsonl"
    cal_cfg = tmp_path / "capture.yaml"
    _write_calibration(cal_cfg)

    # last=2400 -> via calibration -> pixel_y=240; put a Fibo line there.
    _write_fibo(fibo, [_line(240)])
    quotes.write_text(json.dumps(_quote_row(2400.0)) + "\n", encoding="utf-8")

    r = _run(fibo=fibo, quotes=quotes, signals=signals,
             calibration_config=cal_cfg, tolerance_px=3)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "LONG"
    assert rec["fibo_line_y"] == 240
    assert rec["confidence"] == pytest.approx(0.9, abs=0.01)


def test_calibration_far_from_any_line_is_flat(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    quotes = tmp_path / "quotes.jsonl"
    signals = tmp_path / "signals.jsonl"
    cal_cfg = tmp_path / "capture.yaml"
    _write_calibration(cal_cfg)

    # last=2400 -> pixel_y=240, but the only line is at y=100
    _write_fibo(fibo, [_line(100)])
    quotes.write_text(json.dumps(_quote_row(2400.0)) + "\n", encoding="utf-8")

    r = _run(fibo=fibo, quotes=quotes, signals=signals,
             calibration_config=cal_cfg, tolerance_px=5)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "FLAT"


# ----------------------------------------------------------------- fallback

def test_no_calibration_block_falls_back_to_raw_price_as_pixel_y(tmp_path):
    """Preserves the Phase-5 invariant that quotes are treated as
    raw pixel-y values when no calibration is provided. This keeps
    the existing unit tests honest."""
    fibo = tmp_path / "fibo_lines.json"
    quotes = tmp_path / "quotes.jsonl"
    signals = tmp_path / "signals.jsonl"
    cal_cfg = tmp_path / "capture_no_calibration.yaml"
    cal_cfg.write_text("region:\n  monitor_index: 1\n", encoding="utf-8")

    # last=2401 used as raw pixel_y -> matches Fibo at y=2401 (dist=0)
    _write_fibo(fibo, [_line(2401)])
    quotes.write_text(json.dumps(_quote_row(2401.0)) + "\n", encoding="utf-8")

    r = _run(fibo=fibo, quotes=quotes, signals=signals,
             calibration_config=cal_cfg)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "LONG"
    assert rec["fibo_line_y"] == 2401


def test_missing_calibration_config_file_falls_back_to_raw_price(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    quotes = tmp_path / "quotes.jsonl"
    signals = tmp_path / "signals.jsonl"
    cal_cfg = tmp_path / "does_not_exist.yaml"

    _write_fibo(fibo, [_line(2401)])
    quotes.write_text(json.dumps(_quote_row(2401.0)) + "\n", encoding="utf-8")

    r = _run(fibo=fibo, quotes=quotes, signals=signals,
             calibration_config=cal_cfg)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "LONG"


# ----------------------------------------------------------------- bad config

def test_malformed_calibration_block_emits_flat_and_does_not_crash(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    quotes = tmp_path / "quotes.jsonl"
    signals = tmp_path / "signals.jsonl"
    cal_cfg = tmp_path / "bad.yaml"
    cal_cfg.write_text(
        "calibration:\n"
        "  reference_high:\n    pixel_y: 420\n    price: 2450.0\n"  # inverted
        "  reference_low:\n    pixel_y: 60\n    price: 2350.0\n",
        encoding="utf-8",
    )

    _write_fibo(fibo, [_line(2401)])
    quotes.write_text(json.dumps(_quote_row(2401.0)) + "\n", encoding="utf-8")

    r = _run(fibo=fibo, quotes=quotes, signals=signals,
             calibration_config=cal_cfg)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "FLAT"
    assert "calibration" in rec["reason"].lower()


# ----------------------------------------------------------------- calibrate_chart CLI

def test_calibrate_chart_refuses_inverted_axis():
    r = subprocess.run(
        [sys.executable, "-m", "tools.calibrate_chart",
         "--pixel-y-high", "420", "--price-high", "2450",
         "--pixel-y-low",  "60",  "--price-low",  "2350"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert r.returncode != 0
    assert "FAIL" in r.stderr or "FAIL" in r.stdout


def test_calibrate_chart_prints_yaml_block_when_valid():
    r = subprocess.run(
        [sys.executable, "-m", "tools.calibrate_chart",
         "--pixel-y-high", "60",  "--price-high", "2450",
         "--pixel-y-low",  "420", "--price-low",  "2350"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "calibration:" in r.stdout
    assert "reference_high" in r.stdout
    assert "reference_low" in r.stdout
    assert "2450" in r.stdout
    assert "2350" in r.stdout


def test_calibrate_chart_writes_overlay_against_synthetic_image(tmp_path):
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    src = tmp_path / "capture.png"
    cv2.imwrite(str(src), np.full((480, 800, 3), 25, dtype=np.uint8))
    out = tmp_path / "calibration_debug.png"

    r = subprocess.run(
        [sys.executable, "-m", "tools.calibrate_chart",
         "--pixel-y-high", "60",  "--price-high", "2450",
         "--pixel-y-low",  "420", "--price-low",  "2350",
         "--input",  str(src),
         "--output", str(out),
         "--tick",   "10"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert out.exists() and out.stat().st_size > 0
