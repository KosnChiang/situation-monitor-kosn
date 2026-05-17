"""Capture a single TradingView screen region and save it as PNG.

Run:
    python -m tools.capture_test
    python -m tools.capture_test --config config/capture.yaml --out logs/capture_test.png

Reads the region from config/capture.yaml (monitor_index + optional
left/top/width/height). Useful for confirming the capture box covers the
chart area before wiring up the YOLO detector.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _load_yaml(path: Path) -> dict:
    import yaml  # type: ignore

    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _region_from_cfg(cfg: dict):
    from capture.screen_capture import CaptureRegion

    region = cfg.get("region", {}) or {}
    return CaptureRegion(
        monitor_index=int(region.get("monitor_index", 1)),
        left=region.get("left"),
        top=region.get("top"),
        width=region.get("width"),
        height=region.get("height"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/capture.yaml")
    ap.add_argument("--out", default="logs/capture_test.png")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not cfg_path.exists():
        print(f"FAIL: config not found: {cfg_path}", file=sys.stderr)
        return 2

    cfg = _load_yaml(cfg_path)
    region = _region_from_cfg(cfg)

    from capture.screen_capture import ScreenCapture
    import cv2  # type: ignore

    sc = ScreenCapture(region=region)
    try:
        frame = sc.grab()
    finally:
        sc.close()

    ok = cv2.imwrite(str(out_path), frame)
    if not ok:
        print(f"FAIL: cv2.imwrite returned False for {out_path}", file=sys.stderr)
        return 3

    print(
        f"OK: captured monitor={region.monitor_index} "
        f"region=({region.left},{region.top},{region.width}x{region.height}) "
        f"shape={frame.shape} -> {out_path}"
    )
    print(f"CUDA_VISIBLE_DEVICES={os.getenv('CUDA_VISIBLE_DEVICES', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
