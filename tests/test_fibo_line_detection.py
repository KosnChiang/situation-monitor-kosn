"""Tests for the OpenCV Fibo line detector.

We generate a synthetic chart-like image with N horizontal lines at
known y-coordinates and known colours, run the detector, and assert
that each ground-truth line is recovered. Also asserts the detector
introduces no broker SDK imports.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from vision.fibo_line_detector import detect_fibo_lines, draw_debug  # noqa: E402


# Ground-truth Fibo levels we draw into the synthetic image.
GT = [
    (60,  (200, 200, 200)),  # 1.0   - light grey
    (140, (60, 200, 255)),   # 0.786 - amber
    (210, (60, 255, 60)),    # 0.618 - green
    (280, (60, 200, 255)),   # 0.5   - amber
    (350, (255, 80, 80)),    # 0.382 - blue
    (420, (200, 200, 200)),  # 0.0   - light grey
]
IMG_W, IMG_H = 800, 480


def _synthetic_chart():
    img = np.full((IMG_H, IMG_W, 3), 25, dtype=np.uint8)  # near-black background
    # Sprinkle some "candles" so the image isn't trivially clean.
    rng = np.random.default_rng(42)
    for x in range(0, IMG_W, 14):
        h = int(rng.integers(20, 200))
        y0 = int(rng.integers(60, IMG_H - 60 - h))
        color = (180, 180, 180) if rng.random() > 0.5 else (90, 90, 90)
        cv2.rectangle(img, (x + 2, y0), (x + 10, y0 + h), color, -1)
    # Draw the Fibo lines on top, spanning ~90% of the width.
    x1, x2 = int(IMG_W * 0.05), int(IMG_W * 0.95)
    for y, color in GT:
        cv2.line(img, (x1, y), (x2, y), color, 2)
    return img


def test_detects_all_ground_truth_lines():
    img = _synthetic_chart()
    res = detect_fibo_lines(img, min_length_ratio=0.4)

    assert res.image_shape == (IMG_H, IMG_W)
    assert len(res.lines) >= len(GT), f"expected >= {len(GT)} lines, got {len(res.lines)}"

    ys_found = sorted(s.y for s in res.lines)
    for gt_y, _ in GT:
        assert any(abs(gt_y - y) <= 3 for y in ys_found), f"missing line near y={gt_y}, found {ys_found}"


def test_reports_length_and_horizontal_angle():
    img = _synthetic_chart()
    res = detect_fibo_lines(img, min_length_ratio=0.4)
    for s in res.lines:
        assert s.length >= int(IMG_W * 0.4)
        assert abs(s.angle_deg) <= 2.0
        assert s.x_end > s.x_start


def test_reports_colour_close_to_truth():
    img = _synthetic_chart()
    res = detect_fibo_lines(img, min_length_ratio=0.4)

    # Match each detected line to the nearest ground-truth y, then check
    # its colour is within a tolerance of the drawn colour.
    for s in res.lines:
        gt_y, gt_color = min(GT, key=lambda g: abs(g[0] - s.y))
        if abs(gt_y - s.y) > 3:
            continue
        diffs = [abs(int(a) - int(b)) for a, b in zip(s.color_bgr, gt_color)]
        assert max(diffs) <= 60, f"line y={s.y} colour {s.color_bgr} too far from gt {gt_color}"


def test_returns_no_lines_on_empty_canvas():
    blank = np.full((IMG_H, IMG_W, 3), 25, dtype=np.uint8)
    res = detect_fibo_lines(blank)
    assert res.lines == []


def test_rejects_non_bgr_input():
    with pytest.raises(ValueError):
        detect_fibo_lines(np.zeros((10, 10), dtype=np.uint8))  # type: ignore[arg-type]


def test_draw_debug_writes_overlay(tmp_path):
    img = _synthetic_chart()
    res = detect_fibo_lines(img, min_length_ratio=0.4)
    out = draw_debug(img, res)
    assert out.shape == img.shape
    p = tmp_path / "debug.png"
    assert cv2.imwrite(str(p), out)
    assert p.stat().st_size > 0


def test_cli_module_has_no_broker_imports():
    text = (ROOT / "tools" / "detect_fibo_lines.py").read_text(encoding="utf-8")
    forbidden = [
        "ib_insync", "ibapi", "alpaca", "ccxt", "binance",
        "oandapyV20", "MetaTrader5", "shioaji",
    ]
    for name in forbidden:
        assert not re.search(rf"\b{name}\b", text), f"forbidden broker token: {name}"


def test_cli_end_to_end_on_synthetic_capture(tmp_path):
    """Run the same code path the CLI runs, end-to-end."""
    img = _synthetic_chart()
    in_path = tmp_path / "capture_test.png"
    out_path = tmp_path / "fibo_lines_debug.png"
    assert cv2.imwrite(str(in_path), img)

    loaded = cv2.imread(str(in_path), cv2.IMREAD_COLOR)
    res = detect_fibo_lines(loaded, min_length_ratio=0.4)
    overlay = draw_debug(loaded, res)
    assert cv2.imwrite(str(out_path), overlay)

    assert out_path.exists() and out_path.stat().st_size > 0
    assert len(res.lines) >= len(GT)
