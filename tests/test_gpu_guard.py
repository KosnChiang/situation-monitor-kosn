"""Confirm the project defaults CUDA_VISIBLE_DEVICES=1.

Rationale: on a dual-RTX-3090 box card #0 is typically the desktop /
display card. We pin all inference to card #1 so the UI stays
responsive and so detection workloads never compete with the OS
compositor.

This test does NOT require torch — it inspects the config/script
artifacts that the user actually runs.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_env_example_pins_gpu_one():
    txt = _read(".env.example")
    assert re.search(r"^\s*CUDA_VISIBLE_DEVICES\s*=\s*1\s*$", txt, re.MULTILINE), (
        ".env.example must default CUDA_VISIBLE_DEVICES=1"
    )


def test_run_script_pins_gpu_one():
    txt = _read("scripts/run.ps1")
    assert re.search(r'CUDA_VISIBLE_DEVICES\s*=\s*"1"', txt), (
        "scripts/run.ps1 must export CUDA_VISIBLE_DEVICES=1"
    )


def test_fibo_detector_defaults_to_cuda0_after_remap():
    # After CUDA_VISIBLE_DEVICES=1 remaps the chosen card to index 0,
    # the detector should target 'cuda:0' by default.
    txt = _read("vision/fibo_detector.py")
    assert 'FIBO_DEVICE", "cuda:0"' in txt, (
        "FiboDetector default device must be cuda:0 (the remapped 3090 #1)"
    )


def test_setup_script_does_not_pin_gpu_zero():
    # Defensive: make sure no script silently sets DEVICES=0.
    for rel in ("scripts/run.ps1", "scripts/setup_windows.ps1", "scripts/test.ps1", ".env.example"):
        txt = _read(rel)
        assert not re.search(r'CUDA_VISIBLE_DEVICES\s*=\s*"?0"?\b', txt), (
            f"{rel} must not set CUDA_VISIBLE_DEVICES=0"
        )
