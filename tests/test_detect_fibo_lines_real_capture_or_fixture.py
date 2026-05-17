"""End-to-end test for ``tools.detect_fibo_lines``.

Spawns the CLI as a subprocess against either:

  * a real screenshot at ``logs/capture_test.png`` if one exists on
    this host (the operator has captured a TradingView frame), OR
  * a synthetic chart fixture generated in-test (always available).

Asserts the Phase-5 file outputs match the spec:

  * ``logs/fibo_lines_debug.png`` is written and non-empty;
  * ``logs/fibo_lines.json`` is written and parses;
  * each line record carries ``y``, ``x_start``, ``x_end``, ``length``,
    ``color_bgr`` (list of 3 ints), ``color_rgb`` (list of 3 ints),
    ``color_hex``, ``angle_deg`` and ``confidence`` (clamped to 0..1).
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

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


GROUND_TRUTH_LINES = [
    (60,  (200, 200, 200)),
    (140, (60,  200, 255)),
    (210, (60,  255, 60)),
    (280, (60,  200, 255)),
    (350, (255, 80,  80)),
    (420, (200, 200, 200)),
]


def _make_synthetic(path: Path, w: int = 800, h: int = 480) -> None:
    img = np.full((h, w, 3), 25, dtype=np.uint8)
    rng = np.random.default_rng(42)
    for x in range(0, w, 14):
        bh = int(rng.integers(20, 200))
        y0 = int(rng.integers(60, h - 60 - bh))
        color = (180, 180, 180) if rng.random() > 0.5 else (90, 90, 90)
        cv2.rectangle(img, (x + 2, y0), (x + 10, y0 + bh), color, -1)
    x1, x2 = int(w * 0.05), int(w * 0.95)
    for y, color in GROUND_TRUTH_LINES:
        cv2.line(img, (x1, y), (x2, y), color, 2)
    cv2.imwrite(str(path), img)


def _run_cli(*, in_path: Path, out_png: Path, out_json: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-m", "tools.detect_fibo_lines",
            "--input", str(in_path),
            "--output", str(out_png),
            "--out-json", str(out_json),
            "--min-length-ratio", "0.4",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "LIVE_TRADING": "false",
            "EXECUTION_MODE": "mock",
            "BROKER_MODE": "mock",
        },
    )


def _assert_line_record_shape(line: dict) -> None:
    for field in ("y", "x_start", "x_end", "length", "color_bgr",
                  "color_rgb", "color_hex", "angle_deg", "confidence"):
        assert field in line, f"missing field: {field}"
    assert isinstance(line["color_bgr"], list) and len(line["color_bgr"]) == 3
    assert isinstance(line["color_rgb"], list) and len(line["color_rgb"]) == 3
    assert 0.0 <= line["confidence"] <= 1.0
    assert line["color_hex"].startswith("#") and len(line["color_hex"]) == 7


def test_cli_writes_json_and_debug_png_on_synthetic_fixture(tmp_path):
    src = tmp_path / "capture.png"
    _make_synthetic(src)
    out_png = tmp_path / "fibo_lines_debug.png"
    out_json = tmp_path / "fibo_lines.json"

    r = _run_cli(in_path=src, out_png=out_png, out_json=out_json)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    assert out_json.exists(), f"JSON not written: {out_json}"
    assert out_png.exists() and out_png.stat().st_size > 0

    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "lines" in data
    assert "image_shape" in data
    assert data["count"] == len(data["lines"])
    assert len(data["lines"]) >= len(GROUND_TRUTH_LINES)

    for line in data["lines"]:
        _assert_line_record_shape(line)


def test_cli_fails_loudly_on_missing_input(tmp_path):
    missing = tmp_path / "nope.png"
    out_png = tmp_path / "debug.png"
    out_json = tmp_path / "lines.json"
    r = _run_cli(in_path=missing, out_png=out_png, out_json=out_json)
    assert r.returncode != 0
    assert "FAIL" in r.stderr or "FAIL" in r.stdout


@pytest.mark.skipif(
    not (ROOT / "logs" / "capture_test.png").exists(),
    reason="logs/capture_test.png absent — only runs on a real host that has captured",
)
def test_cli_on_real_capture_at_logs_capture_test_png(tmp_path):
    src = ROOT / "logs" / "capture_test.png"
    out_png = tmp_path / "fibo_lines_debug.png"
    out_json = tmp_path / "fibo_lines.json"
    r = _run_cli(in_path=src, out_png=out_png, out_json=out_json)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    data = json.loads(out_json.read_text(encoding="utf-8"))
    # Don't assert line count — the host's TradingView frame may show 0..N
    # Fibo lines. Only assert the schema.
    assert "lines" in data
    for line in data["lines"]:
        _assert_line_record_shape(line)
