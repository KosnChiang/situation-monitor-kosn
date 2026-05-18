"""CLI: validate and visualise a chart-calibration choice.

Workflow for the operator:

  1. Capture a TradingView screenshot (``tools.capture_test``).
  2. Open it, find two price labels (e.g. one near the top, one near
     the bottom). Note each label's pixel-y position (row in the image)
     and the price it represents.
  3. Pass those four numbers to this tool together with the
     screenshot path. The tool:
       * validates the calibration (refuses inverted axes etc.);
       * draws horizontal lines at every ``--tick`` price interval
         and labels each;
       * writes an annotated PNG to ``--output``;
       * prints the YAML block to stdout, ready to paste into
         ``config/capture.yaml`` under a top-level ``calibration:`` key.

Run:
    python -m tools.calibrate_chart \\
        --pixel-y-high 60  --price-high 2450 \\
        --pixel-y-low  420 --price-low  2350 \\
        --input  logs/capture_test.png \\
        --output logs/calibration_debug.png \\
        --tick   10

Pure visualisation tool. Imports no broker SDK, no network library,
no executor. Never modifies ``config/capture.yaml`` -- the operator
copy-pastes the printed YAML block themselves.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vision.chart_calibration import CalibrationError, ChartCalibration


def _yaml_block(cal: ChartCalibration, tick: float) -> str:
    return (
        "calibration:\n"
        f"  reference_high:\n"
        f"    pixel_y: {cal.pixel_y_high}\n"
        f"    price: {cal.price_high}\n"
        f"  reference_low:\n"
        f"    pixel_y: {cal.pixel_y_low}\n"
        f"    price: {cal.price_low}\n"
        f"  # slope ~ {cal.slope_price_per_pixel:.4f} price units per pixel-y\n"
        f"  # tick spacing used for last verify overlay: {tick}\n"
    )


def _draw_overlay(img, cal: ChartCalibration, tick: float):
    import cv2  # type: ignore

    out = img.copy()
    h, w = out.shape[:2]

    # Reference points -- bright cyan crosshair.
    for label, py, price in (
        ("HIGH", cal.pixel_y_high, cal.price_high),
        ("LOW",  cal.pixel_y_low,  cal.price_low),
    ):
        if 0 <= py < h:
            cv2.line(out, (0, py), (w, py), (255, 255, 0), 2)
            cv2.putText(out, f"REF {label} y={py} price={price}",
                        (10, max(14, py - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)

    # Tick ladder -- snap to round multiples of `tick`.
    if tick > 0:
        top_price    = cal.pixel_y_to_price(0)
        bottom_price = cal.pixel_y_to_price(h - 1)
        lo = min(top_price, bottom_price)
        hi = max(top_price, bottom_price)
        first = (int(lo / tick) + 1) * tick
        price = first
        while price <= hi:
            y = int(round(cal.price_to_pixel_y(price)))
            if 0 <= y < h:
                cv2.line(out, (0, y), (w, y), (0, 200, 0), 1)
                cv2.putText(out, f"{price:g}",
                            (w - 90, y - 4),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 200, 0), 1)
            price += tick
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pixel-y-high", type=int, required=True)
    ap.add_argument("--price-high",   type=float, required=True)
    ap.add_argument("--pixel-y-low",  type=int, required=True)
    ap.add_argument("--price-low",    type=float, required=True)
    ap.add_argument("--input",  default=None,
                    help="optional screenshot path for the verification overlay")
    ap.add_argument("--output", default="logs/calibration_debug.png",
                    help="where to write the verification overlay")
    ap.add_argument("--tick",   type=float, default=10.0,
                    help="draw a horizontal grid line every <tick> price units")
    args = ap.parse_args()

    try:
        cal = ChartCalibration(
            pixel_y_high=args.pixel_y_high,
            price_high=args.price_high,
            pixel_y_low=args.pixel_y_low,
            price_low=args.price_low,
        )
    except CalibrationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    # Always print the YAML block first -- even if no --input is given,
    # the operator still gets something they can paste into the config.
    print(_yaml_block(cal, args.tick))

    if not args.input:
        return 0

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"FAIL: input image not found: {in_path}", file=sys.stderr)
        return 3

    import cv2  # type: ignore

    img = cv2.imread(str(in_path), cv2.IMREAD_COLOR)
    if img is None:
        print(f"FAIL: cv2 could not decode {in_path}", file=sys.stderr)
        return 4

    overlay = _draw_overlay(img, cal, args.tick)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), overlay):
        print(f"FAIL: cv2.imwrite returned False for {out_path}", file=sys.stderr)
        return 5

    print(f"verify overlay -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
