"""Unit tests for vision.fibo_line_level_namer.

Bridges Phase-5 CV `FiboLineSegment` lists (with no semantic name) to
Phase-6 `FiboDetection` objects (with canonical Fib level names). Pure
function; no I/O.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")

from vision.fibo_detector import FiboDetection, FiboLevel  # noqa: E402
from vision.fibo_line_level_namer import (  # noqa: E402
    CANONICAL_FIB_SEQUENCE_TOP_TO_BOTTOM,
    MIN_LINES_FOR_NAMING,
    _canonical_labels_for_n,
    name_levels,
)


# ----- shared fixture --------------------------------------------------------

@dataclass
class _Seg:
    """Duck-typed stand-in for vision.fibo_line_detector.FiboLineSegment."""
    y: int
    x_start: int = 0
    x_end: int = 800
    length: int = 800
    confidence: float = 0.9


def _segs(*ys: int, conf: float = 0.9) -> list[_Seg]:
    return [_Seg(y=y, confidence=conf) for y in ys]


def _dict_lines(*ys: int, conf: float = 0.9) -> list[dict]:
    return [
        {"y": y, "x_start": 0, "x_end": 800, "length": 800,
         "color_bgr": [60, 255, 60], "confidence": conf}
        for y in ys
    ]


# ----- canonical sequence ----------------------------------------------------

def test_canonical_sequence_constant_is_top_to_bottom():
    assert CANONICAL_FIB_SEQUENCE_TOP_TO_BOTTOM == (
        "0.0", "0.382", "0.5", "0.618", "0.786", "1.0",
    )


@pytest.mark.parametrize("n,expected", [
    (6, ["0.0", "0.382", "0.5", "0.618", "0.786", "1.0"]),
    (7, ["0.0", "0.382", "0.5", "0.618", "0.786", "1.0"]),
    (5, ["0.0", "0.382", "0.618", "0.786", "1.0"]),
    (4, ["0.0", "0.618", "0.786", "1.0"]),
    (3, ["0.0", "0.618", "1.0"]),
    (2, []),
    (1, []),
    (0, []),
])
def test_canonical_labels_for_n(n, expected):
    assert _canonical_labels_for_n(n) == expected


# ----- six-line happy path ---------------------------------------------------

def test_six_lines_get_all_canonical_levels_top_to_bottom():
    lines = _segs(60, 140, 210, 280, 350, 420)
    det = name_levels(lines, image_shape=(480, 800))
    assert det.ok
    assert det.image_shape == (480, 800)
    assert [lvl.name for lvl in det.levels] == [
        "0.0", "0.382", "0.5", "0.618", "0.786", "1.0",
    ]
    assert [int(lvl.y_pixel) for lvl in det.levels] == [60, 140, 210, 280, 350, 420]


def test_unsorted_input_is_normalised():
    # same y values, scrambled order
    lines = _segs(280, 60, 420, 210, 140, 350)
    det = name_levels(lines)
    assert [int(lvl.y_pixel) for lvl in det.levels] == [60, 140, 210, 280, 350, 420]
    assert det.levels[0].name == "0.0"   # top
    assert det.levels[-1].name == "1.0"  # bottom


def test_seven_lines_keep_only_top_six():
    lines = _segs(60, 100, 140, 210, 280, 350, 420)
    det = name_levels(lines)
    assert len(det.levels) == 6
    assert det.levels[0].name == "0.0"
    assert det.levels[-1].name == "1.0"


# ----- five / four / three line fallbacks -----------------------------------

def test_five_lines_drop_the_0_5_label():
    lines = _segs(60, 140, 210, 350, 420)
    det = name_levels(lines)
    names = [lvl.name for lvl in det.levels]
    assert names == ["0.0", "0.382", "0.618", "0.786", "1.0"]
    assert "0.5" not in names


def test_four_lines_drop_0_5_and_0_382():
    lines = _segs(60, 140, 210, 420)
    det = name_levels(lines)
    names = [lvl.name for lvl in det.levels]
    assert names == ["0.0", "0.618", "0.786", "1.0"]


def test_three_lines_drop_0_786_so_strategy_returns_flat():
    """With only 3 lines we cannot honestly identify 0.786, so we
    deliberately leave it out -- FiboMobV2 then returns FLAT
    ("missing fibo levels"), which is the correct conservative answer.
    """
    lines = _segs(60, 210, 420)
    det = name_levels(lines)
    names = [lvl.name for lvl in det.levels]
    assert names == ["0.0", "0.618", "1.0"]
    assert "0.786" not in names

    # Confirm the downstream strategy actually returns FLAT.
    from strategy.fibo_mob_v2 import FiboMobV2
    sig = FiboMobV2().evaluate(det, last_price=200.0)
    assert sig.side == "FLAT"
    assert "missing" in sig.reason.lower()


# ----- N < MIN_LINES_FOR_NAMING ---------------------------------------------

@pytest.mark.parametrize("n", list(range(0, MIN_LINES_FOR_NAMING)))
def test_below_min_returns_empty_detection(n):
    lines = _segs(*list(range(60, 60 + n * 60, 60)))
    det = name_levels(lines, image_shape=(480, 800))
    assert not det.ok
    assert det.levels == []
    assert det.image_shape == (480, 800)


def test_empty_input_returns_empty_detection():
    det = name_levels([])
    assert not det.ok
    assert det.levels == []


# ----- dict / object duck typing --------------------------------------------

def test_accepts_dicts_as_well_as_objects():
    obj_det = name_levels(_segs(60, 140, 210, 280, 350, 420))
    dict_det = name_levels(_dict_lines(60, 140, 210, 280, 350, 420))
    assert [lvl.name for lvl in obj_det.levels] == [lvl.name for lvl in dict_det.levels]
    assert [int(lvl.y_pixel) for lvl in obj_det.levels] == [int(lvl.y_pixel) for lvl in dict_det.levels]


def test_confidence_propagates_per_line():
    lines = [
        _Seg(y=60, confidence=0.50),
        _Seg(y=140, confidence=0.95),
        _Seg(y=210, confidence=0.30),
        _Seg(y=280, confidence=0.80),
        _Seg(y=350, confidence=0.70),
        _Seg(y=420, confidence=0.99),
    ]
    det = name_levels(lines)
    by_name = {lvl.name: lvl for lvl in det.levels}
    # Top line (y=60, lowest screen y) -> 0.0
    assert by_name["0.0"].confidence == pytest.approx(0.50)
    # Second from top (y=140) -> 0.382
    assert by_name["0.382"].confidence == pytest.approx(0.95)
    # Bottom line (y=420) -> 1.0
    assert by_name["1.0"].confidence == pytest.approx(0.99)


# ----- YOLO injection point --------------------------------------------------

def test_yolo_classifier_when_present_overrides_position_order():
    """If the operator supplies a YOLO classifier and it returns an
    ok detection, the position-order fallback is skipped."""
    fake_det = FiboDetection(
        image_shape=(480, 800),
        levels=[
            FiboLevel(name="0.618", y_pixel=100.0, confidence=0.95),
            FiboLevel(name="0.786", y_pixel=50.0,  confidence=0.95),
            FiboLevel(name="0.0",   y_pixel=300.0, confidence=0.95),
        ],
    )

    def fake_yolo(_img):
        return fake_det

    # Wildly different CV lines -- if the namer falls back, the result
    # would not match `fake_det`.
    lines = _segs(60, 140, 210, 280, 350, 420)
    det = name_levels(lines, image_shape=(480, 800), yolo_classifier=fake_yolo, yolo_image=object())
    assert det is fake_det
    assert [lvl.name for lvl in det.levels] == ["0.618", "0.786", "0.0"]


def test_yolo_classifier_returning_none_falls_back_to_position_order():
    def fake_yolo_unavailable(_img):
        return None

    lines = _segs(60, 140, 210, 280, 350, 420)
    det = name_levels(lines, yolo_classifier=fake_yolo_unavailable, yolo_image=object())
    assert det.ok
    assert [lvl.name for lvl in det.levels] == [
        "0.0", "0.382", "0.5", "0.618", "0.786", "1.0",
    ]


def test_yolo_classifier_returning_not_ok_detection_falls_back():
    empty = FiboDetection(image_shape=(0, 0), levels=[])  # .ok == False

    def fake_yolo_zero(_img):
        return empty

    lines = _segs(60, 140, 210, 280, 350, 420)
    det = name_levels(lines, yolo_classifier=fake_yolo_zero, yolo_image=object())
    assert det is not empty
    assert det.ok
    assert det.levels[0].name == "0.0"


def test_yolo_classifier_that_raises_falls_back_silently():
    def boom(_img):
        raise RuntimeError("CUDA OOM")

    lines = _segs(60, 140, 210, 280, 350, 420)
    det = name_levels(lines, yolo_classifier=boom, yolo_image=object())
    assert det.ok  # fallback worked, no exception bubbled up
    assert det.levels[0].name == "0.0"


def test_yolo_classifier_not_called_when_image_missing():
    calls: list = []

    def fake_yolo(img):
        calls.append(img)
        return None

    lines = _segs(60, 140, 210, 280, 350, 420)
    name_levels(lines, yolo_classifier=fake_yolo, yolo_image=None)
    assert calls == []
