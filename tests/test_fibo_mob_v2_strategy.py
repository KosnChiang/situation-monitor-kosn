"""Branch coverage tests for strategy.fibo_mob_v2.FiboMobV2.evaluate().

The audit found that this strategy class was orphaned -- nothing in the
production pipeline actually called .evaluate() before the Phase-6
wiring. These tests pin down all five decision branches so we can wire
it in with confidence.

Strict mock-only: no executor, no risk gate, no network.
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

from strategy.fibo_mob_v2 import FiboMobV2, Signal  # noqa: E402
from vision.fibo_detector import FiboDetection, FiboLevel  # noqa: E402


def _det(levels_by_name: dict[str, tuple[float, float]]) -> FiboDetection:
    """Build a FiboDetection from {name: (y_pixel, confidence)}."""
    levels = [
        FiboLevel(name=name, y_pixel=y, confidence=conf)
        for name, (y, conf) in levels_by_name.items()
    ]
    return FiboDetection(image_shape=(480, 800), levels=levels)


# ---------------------------------------------------------------- branch 1: no detection

def test_evaluate_returns_flat_when_detection_not_ok():
    """FiboDetection.ok is False when fewer than 2 levels are present."""
    empty = FiboDetection(image_shape=(480, 800), levels=[])
    sig = FiboMobV2().evaluate(empty, last_price=100.0)
    assert isinstance(sig, Signal)
    assert sig.side == "FLAT"
    assert sig.confidence == 0.0
    assert "no detection" in sig.reason.lower()


def test_evaluate_returns_flat_with_only_one_level():
    one = FiboDetection(image_shape=(480, 800),
                        levels=[FiboLevel(name="0.618", y_pixel=200.0, confidence=0.9)])
    sig = FiboMobV2().evaluate(one, last_price=100.0)
    assert sig.side == "FLAT"


# ---------------------------------------------------------------- branch 2: missing required levels

def test_evaluate_returns_flat_when_entry_level_missing():
    """Strategy needs 0.618 (entry), 0.786 (stop), 0.0 (target). If
    0.618 is absent it must FLAT."""
    det = _det({
        "1.0":   (60.0,  0.95),
        "0.786": (140.0, 0.95),
        "0.0":   (420.0, 0.95),
    })
    sig = FiboMobV2().evaluate(det, last_price=100.0)
    assert sig.side == "FLAT"
    assert "missing" in sig.reason.lower()


def test_evaluate_returns_flat_when_stop_level_missing():
    det = _det({
        "1.0":   (60.0,  0.95),
        "0.618": (210.0, 0.95),
        "0.0":   (420.0, 0.95),
    })
    sig = FiboMobV2().evaluate(det, last_price=100.0)
    assert sig.side == "FLAT"
    assert "missing" in sig.reason.lower()


def test_evaluate_returns_flat_when_target_level_missing():
    det = _det({
        "0.786": (140.0, 0.95),
        "0.618": (210.0, 0.95),
    })
    sig = FiboMobV2().evaluate(det, last_price=100.0)
    assert sig.side == "FLAT"


# ---------------------------------------------------------------- branch 3: low confidence

def test_evaluate_returns_flat_when_confidence_below_threshold():
    det = _det({
        "0.786": (140.0, 0.30),    # below
        "0.618": (210.0, 0.95),
        "0.0":   (420.0, 0.95),
    })
    sig = FiboMobV2(min_confidence=0.55).evaluate(det, last_price=100.0)
    assert sig.side == "FLAT"
    assert "low confidence" in sig.reason.lower()
    assert sig.confidence == pytest.approx(0.30)


def test_evaluate_confidence_is_min_of_required_levels():
    det = _det({
        "0.786": (140.0, 0.80),
        "0.618": (210.0, 0.60),    # lowest
        "0.0":   (420.0, 0.90),
    })
    sig = FiboMobV2(min_confidence=0.55).evaluate(det, last_price=100.0)
    assert sig.confidence == pytest.approx(0.60)


# ---------------------------------------------------------------- branch 4: LONG setup

def test_evaluate_returns_long_when_stop_above_entry_above_target():
    """Screen y grows downward. stop=0.786 (deeper retrace, larger y) >
    entry=0.618 > target=0.0 (swing high, smallest y). That is the
    canonical long retracement geometry."""
    det = _det({
        "0.786": (300.0, 0.9),   # largest y (deepest)
        "0.618": (210.0, 0.9),
        "0.0":   (60.0,  0.9),   # smallest y (top)
    })
    sig = FiboMobV2(min_confidence=0.55).evaluate(det, last_price=125.7)
    assert sig.side == "LONG"
    assert sig.confidence == pytest.approx(0.9)
    assert "fibo-mob 0.618" in sig.reason
    # entry / stop / target are filled with last_price by the v2 contract.
    assert sig.entry == pytest.approx(125.7)


# ---------------------------------------------------------------- branch 5: SHORT setup

def test_evaluate_returns_short_when_stop_below_entry_below_target():
    """Inverse geometry: stop above entry above target on the chart
    (= stop smaller y < entry smaller y < target larger y on screen)."""
    det = _det({
        "0.786": (60.0,  0.9),    # smallest y (top)
        "0.618": (210.0, 0.9),
        "0.0":   (420.0, 0.9),    # largest y (bottom)
    })
    sig = FiboMobV2(min_confidence=0.55).evaluate(det, last_price=200.0)
    assert sig.side == "SHORT"
    assert sig.confidence == pytest.approx(0.9)


# ---------------------------------------------------------------- branch 6: levels not ordered

def test_evaluate_returns_flat_when_levels_not_strictly_ordered():
    """If 0.786 / 0.618 / 0.0 don't form a strict monotonic chain in
    either direction, the strategy refuses with 'levels not ordered'."""
    det = _det({
        "0.786": (200.0, 0.9),
        "0.618": (200.0, 0.9),    # equal -- breaks strict inequality
        "0.0":   (60.0,  0.9),
    })
    sig = FiboMobV2(min_confidence=0.55).evaluate(det, last_price=100.0)
    assert sig.side == "FLAT"
    assert "not ordered" in sig.reason.lower()


def test_evaluate_returns_flat_on_scrambled_levels():
    """e.g. entry between target and stop on the wrong side."""
    det = _det({
        "0.786": (100.0, 0.9),
        "0.618": (50.0,  0.9),    # entry above stop AND above target
        "0.0":   (60.0,  0.9),
    })
    sig = FiboMobV2(min_confidence=0.55).evaluate(det, last_price=100.0)
    assert sig.side == "FLAT"


# ---------------------------------------------------------------- strategy is callable from receivers

def test_can_be_constructed_with_custom_min_confidence():
    s = FiboMobV2(min_confidence=0.99)
    assert s.min_confidence == 0.99
    det = _det({
        "0.786": (300.0, 0.95),
        "0.618": (210.0, 0.95),
        "0.0":   (60.0,  0.95),
    })
    # 0.95 < 0.99
    assert s.evaluate(det, last_price=100.0).side == "FLAT"


def test_signal_dataclass_has_expected_fields():
    det = _det({
        "0.786": (300.0, 0.9),
        "0.618": (210.0, 0.9),
        "0.0":   (60.0,  0.9),
    })
    sig = FiboMobV2().evaluate(det, last_price=42.0)
    for field in ("side", "entry", "stop", "target", "confidence", "reason"):
        assert hasattr(sig, field), f"Signal missing field: {field}"
