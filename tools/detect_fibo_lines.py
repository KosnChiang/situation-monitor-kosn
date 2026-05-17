"""CLI: detect Fibo lines in a TradingView screenshot.

Run:
    python -m tools.detect_fibo_lines
    python -m tools.detect_fibo_lines --input logs/capture_test.png \
                                       --output logs/fibo_lines_debug.png

Reads a BGR PNG, runs the CV pipeline in vision.fibo_line_detector,
prints one line per detected segment (y, length, colour, angle), and
writes an annotated debug PNG.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="logs/capture_test.png")
    ap.add_argument("--output", default="logs/fibo_lines_debug.png")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--min-length-ratio", type=float, default=0.3)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        print(
            f"FAIL: input not found: {in_path}\n"
            f"Hint: run `python -m tools.capture_test` first to produce it.",
            file=sys.stderr,
        )
        return 2

    import cv2  # type: ignore

    img = cv2.imread(str(in_path), cv2.IMREAD_COLOR)
    if img is None:
        print(f"FAIL: cv2 could not decode {in_path}", file=sys.stderr)
        return 3

    from vision.fibo_line_detector import detect_fibo_lines, draw_debug

    result = detect_fibo_lines(img, min_length_ratio=args.min_length_ratio)

    if args.json:
        print(
            json.dumps(
                {
                    "image_shape": result.image_shape,
                    "count": len(result.lines),
                    "lines": [
                        {
                            "y": s.y,
                            "x_start": s.x_start,
                            "x_end": s.x_end,
                            "length": s.length,
                            "color_bgr": list(s.color_bgr),
                            "color_hex": s.color_hex,
                            "angle_deg": round(s.angle_deg, 3),
                        }
                        for s in result.lines
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"image: {result.image_shape[1]}x{result.image_shape[0]}  lines: {len(result.lines)}")
        print(f"{'idx':>3}  {'y':>5}  {'len':>5}  {'angle':>7}  color")
        for i, s in enumerate(result.lines):
            print(f"{i:>3}  {s.y:>5}  {s.length:>5}  {s.angle_deg:>+6.2f}  {s.color_hex}  bgr={s.color_bgr}")

    debug = draw_debug(img, result)
    ok = cv2.imwrite(str(out_path), debug)
    if not ok:
        print(f"FAIL: cv2.imwrite returned False for {out_path}", file=sys.stderr)
        return 4
    print(f"\nwrote debug overlay -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
