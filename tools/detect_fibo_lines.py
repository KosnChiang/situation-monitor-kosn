"""CLI: detect Fibo lines in a TradingView screenshot.

Run:
    python -m tools.detect_fibo_lines
    python -m tools.detect_fibo_lines --input logs/capture_test.png \
                                       --output logs/fibo_lines_debug.png \
                                       --out-json logs/fibo_lines.json

Reads a BGR PNG, runs the CV pipeline in vision.fibo_line_detector,
ALWAYS writes the structured JSON to ``--out-json`` (default
``logs/fibo_lines.json``), ALWAYS writes the annotated debug PNG to
``--output``, and prints a short summary (or full JSON, with ``--json``)
to stdout. Each detected line carries: y, x_start, x_end, length,
color_bgr, color_rgb, color_hex, angle_deg, confidence.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _line_dict(seg) -> dict:
    return {
        "y": seg.y,
        "x_start": seg.x_start,
        "x_end": seg.x_end,
        "length": seg.length,
        "color_bgr": list(seg.color_bgr),
        "color_rgb": list(seg.color_rgb),
        "color_hex": seg.color_hex,
        "angle_deg": round(seg.angle_deg, 3),
        "confidence": round(seg.confidence, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="logs/capture_test.png")
    ap.add_argument("--output", default="logs/fibo_lines_debug.png")
    ap.add_argument("--out-json", default="logs/fibo_lines.json")
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout instead of summary text")
    ap.add_argument("--min-length-ratio", type=float, default=0.3)
    args = ap.parse_args()

    in_path = Path(args.input)
    out_png = Path(args.output)
    out_json = Path(args.out_json)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_json.parent.mkdir(parents=True, exist_ok=True)

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

    payload = {
        "input": str(in_path),
        "image_shape": list(result.image_shape),
        "count": len(result.lines),
        "lines": [_line_dict(s) for s in result.lines],
    }

    out_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    debug = draw_debug(img, result)
    ok = cv2.imwrite(str(out_png), debug)
    if not ok:
        print(f"FAIL: cv2.imwrite returned False for {out_png}", file=sys.stderr)
        return 4

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"image: {result.image_shape[1]}x{result.image_shape[0]}  lines: {len(result.lines)}")
        print(f"{'idx':>3}  {'y':>5}  {'len':>5}  {'angle':>7}  {'conf':>5}  color")
        for i, s in enumerate(result.lines):
            print(
                f"{i:>3}  {s.y:>5}  {s.length:>5}  "
                f"{s.angle_deg:>+6.2f}  {s.confidence:>5.2f}  "
                f"{s.color_hex}  bgr={s.color_bgr} rgb={s.color_rgb}"
            )
        print(f"\nwrote JSON      -> {out_json}")
        print(f"wrote debug PNG -> {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
