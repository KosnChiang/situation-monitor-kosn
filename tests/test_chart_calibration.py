"""Tests for vision.chart_calibration.

Pure math + YAML parsing. No subprocess, no CV deps required.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")

from vision.chart_calibration import CalibrationError, ChartCalibration  # noqa: E402


def _cal():
    return ChartCalibration(
        pixel_y_high=60, price_high=2450.0,
        pixel_y_low=420, price_low=2350.0,
    )


# ----------------------------------------------------------------- construction

def test_construction_rejects_inverted_y_axis():
    # pixel_y_high must be smaller (nearer top) than pixel_y_low
    with pytest.raises(CalibrationError):
        ChartCalibration(pixel_y_high=420, price_high=2450, pixel_y_low=60, price_low=2350)


def test_construction_rejects_inverted_price_axis():
    # price at the higher pixel-y must be the lower price
    with pytest.raises(CalibrationError):
        ChartCalibration(pixel_y_high=60, price_high=2350, pixel_y_low=420, price_low=2450)


def test_construction_rejects_equal_pixel_y():
    with pytest.raises(CalibrationError):
        ChartCalibration(pixel_y_high=60, price_high=2450, pixel_y_low=60, price_low=2350)


def test_construction_rejects_non_integer_pixel_y():
    with pytest.raises(CalibrationError):
        ChartCalibration(pixel_y_high=60.5, price_high=2450, pixel_y_low=420, price_low=2350)  # type: ignore[arg-type]


# ----------------------------------------------------------------- math

def test_slope_is_negative():
    cal = _cal()
    assert cal.slope_price_per_pixel < 0


def test_endpoint_round_trip():
    cal = _cal()
    assert cal.pixel_y_to_price(60)  == pytest.approx(2450.0)
    assert cal.pixel_y_to_price(420) == pytest.approx(2350.0)
    assert cal.price_to_pixel_y(2450.0) == pytest.approx(60)
    assert cal.price_to_pixel_y(2350.0) == pytest.approx(420)


def test_midpoint_round_trip():
    cal = _cal()
    mid_y = (60 + 420) / 2
    mid_price = (2450 + 2350) / 2
    assert cal.pixel_y_to_price(mid_y) == pytest.approx(mid_price)
    assert cal.price_to_pixel_y(mid_price) == pytest.approx(mid_y)


def test_round_trips_for_arbitrary_prices():
    cal = _cal()
    for p in (2350.0, 2375.5, 2400.0, 2412.3, 2449.9):
        y = cal.price_to_pixel_y(p)
        assert cal.pixel_y_to_price(y) == pytest.approx(p)


# ----------------------------------------------------------------- yaml

def test_from_yaml_loads_valid_block(tmp_path):
    p = tmp_path / "capture.yaml"
    p.write_text(
        "calibration:\n"
        "  reference_high:\n    pixel_y: 60\n    price: 2450.0\n"
        "  reference_low:\n    pixel_y: 420\n    price: 2350.0\n",
        encoding="utf-8",
    )
    cal = ChartCalibration.from_yaml(p)
    assert cal is not None
    assert cal.pixel_y_to_price(60) == pytest.approx(2450.0)


def test_from_yaml_returns_none_when_block_absent(tmp_path):
    p = tmp_path / "capture.yaml"
    p.write_text("region:\n  monitor_index: 1\n", encoding="utf-8")
    assert ChartCalibration.from_yaml(p) is None


def test_from_yaml_returns_none_when_block_is_explicitly_null(tmp_path):
    p = tmp_path / "capture.yaml"
    p.write_text("calibration:\n", encoding="utf-8")
    assert ChartCalibration.from_yaml(p) is None


def test_from_yaml_raises_on_missing_field(tmp_path):
    p = tmp_path / "capture.yaml"
    p.write_text(
        "calibration:\n"
        "  reference_high:\n    pixel_y: 60\n"     # missing price
        "  reference_low:\n    pixel_y: 420\n    price: 2350.0\n",
        encoding="utf-8",
    )
    with pytest.raises(CalibrationError):
        ChartCalibration.from_yaml(p)


def test_from_yaml_raises_on_inverted_block(tmp_path):
    p = tmp_path / "capture.yaml"
    p.write_text(
        "calibration:\n"
        "  reference_high:\n    pixel_y: 420\n    price: 2450.0\n"
        "  reference_low:\n    pixel_y: 60\n    price: 2350.0\n",
        encoding="utf-8",
    )
    with pytest.raises(CalibrationError):
        ChartCalibration.from_yaml(p)


def test_from_yaml_raises_when_file_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        ChartCalibration.from_yaml(tmp_path / "nope.yaml")
