"""Graceful-degradation tests for vision.fibo_detector.FiboDetector.try_detect.

The Phase-6 namer (vision.fibo_line_level_namer.name_levels) is allowed
to be called with a YOLO classifier that may not be runnable on the
current host. FiboDetector.try_detect must return None instead of
raising in those cases so the position-order fallback kicks in.

In this Linux test container neither torch nor ultralytics is installed,
so test_try_detect_returns_none_when_torch_missing exercises the real
ImportError path. The "weights missing" path is exercised directly.

The .detect() method (strict, raising) remains untested here -- the
operator's Windows host runs it for real and we do not have weights.
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

from vision.fibo_detector import FiboDetection, FiboDetector, FiboLevel  # noqa: E402


# ---------------------------------------------------------------- env-driven graceful path

def test_try_detect_returns_none_when_torch_missing():
    """The container has no torch / ultralytics. .try_detect() must
    swallow the ImportError and return None so callers can fall back."""
    try:
        import torch  # noqa: F401
        pytest.skip("torch is installed on this host; this test exercises the missing-torch path")
    except ImportError:
        pass

    det = FiboDetector(weights="this/weights/path/does/not/matter.pt")
    result = det.try_detect(frame=object())
    assert result is None


def test_try_detect_returns_none_when_weights_missing(monkeypatch, tmp_path):
    """Even if torch / ultralytics import OK, missing weights file ->
    None. Simulated by mocking the import successful."""
    # Make the import succeed even if torch is missing
    fake_torch = type(sys)("torch")
    fake_torch.__version__ = "0.0.0-test"
    fake_ultra = type(sys)("ultralytics")
    fake_ultra.YOLO = lambda *a, **kw: None  # never called
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "ultralytics", fake_ultra)

    det = FiboDetector(weights=str(tmp_path / "does_not_exist.pt"))
    assert det.try_detect(frame=object()) is None


def test_try_detect_returns_none_when_detect_raises(monkeypatch, tmp_path):
    """If torch + ultralytics + weights all present but the YOLO call
    raises (corrupted weights, CUDA OOM, etc.), .try_detect returns
    None instead of bubbling the error."""
    fake_torch = type(sys)("torch")
    fake_torch.__version__ = "0.0.0-test"
    fake_ultra = type(sys)("ultralytics")
    fake_ultra.YOLO = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "ultralytics", fake_ultra)

    weights = tmp_path / "fake.pt"
    weights.write_bytes(b"not a real model")

    det = FiboDetector(weights=str(weights))

    def boom(_frame):
        raise RuntimeError("CUDA OOM")
    monkeypatch.setattr(det, "detect", boom)

    assert det.try_detect(frame=object()) is None


# ---------------------------------------------------------------- namer integration

def test_namer_uses_try_detect_via_callable_injection():
    """The namer accepts an arbitrary classifier callable. Confirm
    plugging FiboDetector.try_detect works end-to-end (returns None in
    this env, so the namer falls back to position-order)."""
    from vision.fibo_line_level_namer import name_levels

    det = FiboDetector(weights="/nonexistent/path/fibo.pt")

    class _Seg:
        def __init__(self, y):
            self.y = y; self.x_start = 0; self.x_end = 800
            self.length = 800; self.confidence = 0.9

    fake_image = object()
    result = name_levels(
        [_Seg(y) for y in (60, 140, 210, 280, 350, 420)],
        image_shape=(480, 800),
        yolo_classifier=det.try_detect,
        yolo_image=fake_image,
    )
    # Container has no torch -> try_detect returns None -> position-order kicks in
    assert result.ok
    assert [lvl.name for lvl in result.levels] == [
        "0.0", "0.382", "0.5", "0.618", "0.786", "1.0",
    ]


def test_namer_uses_yolo_when_try_detect_returns_real_detection(monkeypatch):
    """If a future host has torch + weights and YOLO actually
    classifies, the namer must use that output instead of position-order.
    Simulated by replacing try_detect with a deterministic stub."""
    from vision.fibo_line_level_namer import name_levels

    yolo_out = FiboDetection(
        image_shape=(480, 800),
        levels=[
            FiboLevel(name="0.618", y_pixel=100.0, confidence=0.95),
            FiboLevel(name="0.786", y_pixel=50.0,  confidence=0.95),
            FiboLevel(name="0.0",   y_pixel=300.0, confidence=0.95),
        ],
    )

    class _Seg:
        def __init__(self, y):
            self.y = y; self.x_start = 0; self.x_end = 800
            self.length = 800; self.confidence = 0.9

    result = name_levels(
        [_Seg(y) for y in (60, 140, 210, 280, 350, 420)],
        image_shape=(480, 800),
        yolo_classifier=lambda _img: yolo_out,
        yolo_image=object(),
    )
    assert result is yolo_out
    assert [lvl.name for lvl in result.levels] == ["0.618", "0.786", "0.0"]
