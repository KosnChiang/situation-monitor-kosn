"""CLI: filter ``logs/fibo_lines.json`` down to plausible Fibo / user lines.

Run:
    python -m tools.filter_fibo_lines
    python -m tools.filter_fibo_lines --in logs/fibo_lines.json \
                                       --out logs/fibo_lines_filtered.json \
                                       --top-k 8

Reads the raw detector output, applies vision.fibo_line_filter rules,
writes a structured JSON with kept + rejected lists, and prints a short
summary. The downstream ``tools.mock_fibo_signal`` can be pointed at the
filtered file via its existing ``--fibo-lines`` argument; this CLI does
not touch any executor / risk / quote code.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from vision.fibo_line_filter import FilterParams, filter_fibo_lines


def _build_params(args) -> FilterParams:
    palette = tuple(c.strip() for c in args.palette.split(",") if c.strip())
    return FilterParams(
        margin_top=args.margin_top,
        margin_bottom=args.margin_bottom,
        min_value=args.min_value,
        min_chroma=args.min_chroma,
        blue_dark_grid=not args.no_blue_dark_grid,
        chroma_blue_bias=args.chroma_blue_bias,
        blue_dark_max_value_byte=args.blue_dark_max_value_byte,
        palette=palette,
        palette_bonus=args.palette_bonus,
        palette_color_distance=args.palette_color_distance,
        top_k=args.top_k,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", default="logs/fibo_lines.json")
    ap.add_argument("--out", dest="out_path", default="logs/fibo_lines_filtered.json")
    ap.add_argument("--margin-top", type=float, default=0.05)
    ap.add_argument("--margin-bottom", type=float, default=0.07)
    ap.add_argument("--min-value", type=float, default=0.35)
    ap.add_argument("--min-chroma", type=int, default=25)
    ap.add_argument("--no-blue-dark-grid", action="store_true")
    ap.add_argument("--chroma-blue-bias", type=int, default=10)
    ap.add_argument("--blue-dark-max-value-byte", type=int, default=80)
    ap.add_argument("--palette", default="#4CAF50,#FF9800,#F44336,#2196F3,#FFEB3B")
    ap.add_argument("--palette-bonus", type=float, default=0.20)
    ap.add_argument("--palette-color-distance", type=int, default=30)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout instead of summary text")
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not in_path.exists():
        print(
            f"FAIL: input not found: {in_path}\n"
            f"Hint: run `python -m tools.detect_fibo_lines` first to produce it.",
            file=sys.stderr,
        )
        return 2

    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: {in_path} is not valid JSON: {exc}", file=sys.stderr)
        return 3

    if not isinstance(data, dict) or "lines" not in data or "image_shape" not in data:
        print(f"FAIL: {in_path} does not look like detect_fibo_lines output", file=sys.stderr)
        return 4

    shape = tuple(data["image_shape"])
    if len(shape) != 2:
        print(f"FAIL: image_shape must be (h, w), got {shape!r}", file=sys.stderr)
        return 5

    params = _build_params(args)
    result = filter_fibo_lines(data.get("lines", []), shape, params)

    payload = {
        "source": str(in_path),
        "filtered_at": datetime.now(timezone.utc).isoformat(),
        "image_shape": list(result.image_shape),
        "params": params.to_dict(),
        "input_count": result.input_count,
        "kept_count": len(result.kept),
        "dropped_count": len(result.rejected),
        "lines": [L.to_dict() for L in result.kept],
        "rejected": [R.to_dict() for R in result.rejected],
    }

    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"input: {in_path}  shape={result.image_shape[1]}x{result.image_shape[0]}  "
            f"in={result.input_count}  kept={len(result.kept)}  dropped={len(result.rejected)}"
        )
        if result.kept:
            print(f"{'idx':>3}  {'y':>5}  {'len':>5}  {'conf':>5}  {'score':>5}  color")
            for i, L in enumerate(result.kept):
                print(
                    f"{i:>3}  {L.y:>5}  {L.length:>5}  "
                    f"{L.confidence:>5.2f}  {L.score:>5.2f}  {L.color_hex}"
                )
        print(f"\nwrote -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
