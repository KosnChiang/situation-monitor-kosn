"""TMF P&L math tests (Phase 7.A)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from ai_swing.tmf_pnl import (  # noqa: E402
    POINT_VALUE_TWD,
    protected_profit_twd,
    r_multiple,
    risk_twd,
    unrealized_points,
    unrealized_twd,
)


def test_point_value_is_ten():
    assert POINT_VALUE_TWD == 10.0


def test_unrealized_long_70_points_equals_700_twd():
    assert unrealized_points("LONG", 23010, 23080) == pytest.approx(70.0)
    assert unrealized_twd("LONG", 23010, 23080, qty=1) == pytest.approx(700.0)


def test_unrealized_short_70_points_equals_700_twd():
    assert unrealized_points("SHORT", 23080, 23010) == pytest.approx(70.0)
    assert unrealized_twd("SHORT", 23080, 23010, qty=1) == pytest.approx(700.0)


def test_unrealized_long_qty_2_scales():
    assert unrealized_twd("LONG", 23010, 23080, qty=2) == pytest.approx(1400.0)


def test_unrealized_negative_when_against_position():
    assert unrealized_points("LONG", 23010, 22980) == pytest.approx(-30.0)
    assert unrealized_twd("LONG", 23010, 22980, qty=1) == pytest.approx(-300.0)


def test_risk_15_points_qty_1_equals_150_twd():
    assert risk_twd(23010, 22995, qty=1) == pytest.approx(150.0)


def test_risk_long_or_short_same_abs():
    assert risk_twd(23010, 22995, qty=1) == risk_twd(23010, 23025, qty=1)


def test_protected_profit_long_zero_before_breakeven():
    # entry 23010, stop 23000 -> stop still below entry -> not protected
    assert protected_profit_twd("LONG", 23010, 23000) == 0.0


def test_protected_profit_long_after_breakeven_with_buffer():
    # entry 23010, current_stop 23030, buffer=2 -> protected_points = 18
    assert protected_profit_twd("LONG", 23010, 23030) == pytest.approx(180.0)


def test_protected_profit_short_after_breakeven():
    # entry 23080, current_stop 23060, buffer=2 -> protected_points = 18
    assert protected_profit_twd("SHORT", 23080, 23060) == pytest.approx(180.0)


def test_r_multiple_long_2x():
    # entry 23010, stop 22995 (15 points risk), price 23040 (30 points gain)
    assert r_multiple("LONG", 23010, 23040, 22995) == pytest.approx(2.0)


def test_r_multiple_short_2x():
    assert r_multiple("SHORT", 23080, 23050, 23095) == pytest.approx(2.0)


def test_r_multiple_zero_when_no_initial_risk():
    assert r_multiple("LONG", 23010, 23080, 23010) == 0.0
