"""Tests for vision.fibo_line_filter and tools.filter_fibo_lines.

The unit cases use small synthetic line dicts. The integration case
loads the real 18-line JSON captured on the Windows trading box
(tests/fixtures/fibo_lines_18.json, 3840x2160 TradingView maximised on
monitor 2) and asserts the defaults reduce it to exactly the one
visible Material-green Fibo line at y=1533.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")
os.environ.setdefault("BROKER_MODE", "mock")

from vision.fibo_line_filter import (  # noqa: E402
    DEFAULT_PALETTE,
    FilterParams,
    filter_fibo_lines,
)

FIXTURE_18 = ROOT / "tests" / "fixtures" / "fibo_lines_18.json"


def _line(
    y: int,
    rgb: tuple[int, int, int],
    *,
    length: int = 1000,
    confidence: float = 0.9,
    x_start: int = 0,
) -> dict:
    r, g, b = rgb
    return {
        "y": y,
        "x_start": x_start,
        "x_end": x_start + length,
        "length": length,
        "color_bgr": [b, g, r],
        "color_rgb": [r, g, b],
        "color_hex": f"#{r:02X}{g:02X}{b:02X}",
        "angle_deg": 0.0,
        "confidence": confidence,
    }


# Real, distinct colours so unit cases don't accidentally exercise
# multiple rules at once. "MID" is a bright magenta that defeats every
# greyscale / dark / blue-dominant rule but is NOT in the palette.
GREEN = (76, 175, 80)     # #4CAF50, in palette
MID   = (200, 30, 180)    # #C81EB4, bright magenta, not in palette
DARK  = (30, 30, 51)      # #1E1E33, TV dark-navy grid
GREY  = (46, 46, 46)      # #2E2E2E, pure grey


def test_drops_top_ui_band():
    lines = [_line(10, GREEN), _line(500, GREEN)]
    res = filter_fibo_lines(lines, image_shape=(1000, 1000))
    assert [L.y for L in res.kept] == [500]
    assert "rule_a" in res.rejected[0].reason


def test_drops_bottom_ui_band():
    lines = [_line(990, GREEN), _line(500, GREEN)]
    res = filter_fibo_lines(lines, image_shape=(1000, 1000))
    assert [L.y for L in res.kept] == [500]
    assert "rule_a" in res.rejected[0].reason


def test_drops_low_value_dark_grid():
    lines = [_line(500, DARK), _line(500, GREEN)]
    res = filter_fibo_lines(lines, image_shape=(1000, 1000))
    assert [L.color_hex for L in res.kept] == ["#4CAF50"]
    assert any("rule_b" in r.reason for r in res.rejected)


def test_drops_pure_greyscale():
    # Bright greyscale defeats Rule B (V high) but should hit Rule C
    # (chroma=0). Use #969696 = (150,150,150).
    lines = [_line(500, (150, 150, 150)), _line(500, GREEN)]
    res = filter_fibo_lines(lines, image_shape=(1000, 1000))
    assert [L.color_hex for L in res.kept] == ["#4CAF50"]
    assert any("rule_c" in r.reason for r in res.rejected)


def test_drops_blue_dominant_dark_grid_via_rule_d():
    # Make a borderline navy that passes Rule B (V == min_value byte)
    # but is blue-dominant and dark -> Rule D catches it. V=70/255=0.275
    # is below default 0.35, so it'd already be caught by B; lift V to
    # exactly 0.35 = 89 to isolate Rule D.
    navy = (30, 50, 89)  # B>R, V=89=0.349 ~ min_value
    params = FilterParams(min_value=0.0, min_chroma=0, blue_dark_max_value_byte=100)
    lines = [_line(500, navy), _line(500, GREEN)]
    res = filter_fibo_lines(lines, image_shape=(1000, 1000), params=params)
    assert [L.color_hex for L in res.kept] == ["#4CAF50"]
    assert any("rule_d" in r.reason for r in res.rejected)


def test_palette_bonus_promotes_material_green_above_non_palette_peer():
    # Same length & confidence; green is in palette, magenta isn't.
    # Green should rank higher in `kept` ordering.
    lines = [_line(400, MID), _line(600, GREEN)]
    res = filter_fibo_lines(lines, image_shape=(1000, 1000))
    assert len(res.kept) == 2
    assert res.kept[0].color_hex == "#4CAF50"
    assert res.kept[0].score > res.kept[1].score


def test_top_k_truncates_results():
    lines = [_line(100 + 50 * i, GREEN, confidence=0.9 - 0.01 * i) for i in range(10)]
    params = FilterParams(top_k=3)
    res = filter_fibo_lines(lines, image_shape=(1000, 1000), params=params)
    assert len(res.kept) == 3
    scores = [L.score for L in res.kept]
    assert scores == sorted(scores, reverse=True)


def test_kept_line_dict_carries_all_input_fields_plus_score():
    res = filter_fibo_lines([_line(500, GREEN)], image_shape=(1000, 1000))
    assert len(res.kept) == 1
    d = res.kept[0].to_dict()
    for key in (
        "y", "x_start", "x_end", "length",
        "color_bgr", "color_rgb", "color_hex",
        "angle_deg", "confidence", "score", "score_breakdown",
    ):
        assert key in d, f"kept-line dict missing field: {key}"
    assert set(d["score_breakdown"]) == {"confidence", "length", "chroma", "palette_bonus"}


def test_rejected_line_carries_human_reason():
    res = filter_fibo_lines([_line(10, GREEN)], image_shape=(1000, 1000))
    assert res.rejected
    assert res.rejected[0].to_dict()["reason"].startswith("rule_a")


def test_empty_input_returns_empty_result():
    res = filter_fibo_lines([], image_shape=(1000, 1000))
    assert res.input_count == 0
    assert res.kept == [] and res.rejected == []


def test_invalid_image_shape_raises():
    with pytest.raises(ValueError):
        filter_fibo_lines([], image_shape=(0, 0))


def test_default_palette_includes_material_green():
    assert "#4CAF50" in DEFAULT_PALETTE


# ---------------------------------------------------------------------------
# Integration: real captured fixture from the Windows trading box.
# ---------------------------------------------------------------------------

def test_real_fixture_default_params_yield_only_material_green():
    payload = json.loads(FIXTURE_18.read_text(encoding="utf-8"))
    assert payload["count"] == 18, "fixture drifted; regenerate from a real capture"

    res = filter_fibo_lines(payload["lines"], tuple(payload["image_shape"]))
    assert res.input_count == 18
    assert len(res.kept) == 1, [L.color_hex for L in res.kept]
    only = res.kept[0]
    assert only.color_hex == "#4CAF50"
    assert only.y == 1533


def test_real_fixture_explains_every_rejection():
    payload = json.loads(FIXTURE_18.read_text(encoding="utf-8"))
    res = filter_fibo_lines(payload["lines"], tuple(payload["image_shape"]))
    assert len(res.rejected) == 17
    for r in res.rejected:
        assert re.match(r"rule_[abcd]", r.reason), f"unexplained drop: {r.reason}"


def test_filter_is_idempotent_on_its_own_output():
    payload = json.loads(FIXTURE_18.read_text(encoding="utf-8"))
    res1 = filter_fibo_lines(payload["lines"], tuple(payload["image_shape"]))
    kept_dicts = [L.to_dict() for L in res1.kept]
    res2 = filter_fibo_lines(kept_dicts, tuple(payload["image_shape"]))
    assert len(res2.kept) == len(res1.kept)
    assert [L.color_hex for L in res2.kept] == [L.color_hex for L in res1.kept]


# ---------------------------------------------------------------------------
# Module hygiene guards (mock-only envelope).
# ---------------------------------------------------------------------------

FORBIDDEN_BROKER = [
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]
FORBIDDEN_NETWORK = ["requests", "httpx", "aiohttp", "urllib3", "socket", "urllib.request"]
FORBIDDEN_HEAVY = ["cv2", "numpy", "torch", "ultralytics"]


@pytest.mark.parametrize("rel_path", [
    "vision/fibo_line_filter.py",
    "tools/filter_fibo_lines.py",
])
def test_no_broker_or_network_imports(rel_path):
    text = (ROOT / rel_path).read_text(encoding="utf-8")
    for name in FORBIDDEN_BROKER + FORBIDDEN_NETWORK:
        assert not re.search(rf"^\s*(?:import|from)\s+{re.escape(name)}\b", text, re.MULTILINE), (
            f"{rel_path}: forbidden import {name}"
        )


def test_filter_module_is_stdlib_only():
    """The filter is supposed to be a pure stdlib transform so it can
    run in any sandbox without cv2 / numpy / torch. Guard that intent."""
    text = (ROOT / "vision" / "fibo_line_filter.py").read_text(encoding="utf-8")
    for name in FORBIDDEN_HEAVY:
        assert not re.search(rf"^\s*(?:import|from)\s+{re.escape(name)}\b", text, re.MULTILINE), (
            f"vision/fibo_line_filter.py must stay stdlib-only; found {name}"
        )


# ---------------------------------------------------------------------------
# End-to-end CLI test (no display, no broker, no network).
# ---------------------------------------------------------------------------

def test_cli_end_to_end_writes_filtered_json(tmp_path):
    in_json = tmp_path / "fibo_lines.json"
    out_json = tmp_path / "fibo_lines_filtered.json"
    in_json.write_text(FIXTURE_18.read_text(encoding="utf-8"), encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LIVE_TRADING"] = "false"
    env["EXECUTION_MODE"] = "mock"
    env["BROKER_MODE"] = "mock"

    r = subprocess.run(
        [sys.executable, "-m", "tools.filter_fibo_lines",
         "--in", str(in_json), "--out", str(out_json)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, f"CLI failed: stdout={r.stdout!r}  stderr={r.stderr!r}"
    assert out_json.exists()

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["input_count"] == 18
    assert payload["kept_count"] == 1
    assert payload["dropped_count"] == 17
    assert payload["lines"][0]["color_hex"] == "#4CAF50"
    assert payload["lines"][0]["y"] == 1533
    assert set(payload["params"]) >= {"margin_top", "min_value", "min_chroma", "top_k"}
