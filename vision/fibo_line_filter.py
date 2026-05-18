"""Filter raw horizontal-line detections down to plausible Fibo / user lines.

Consumes the JSON shape produced by ``tools.detect_fibo_lines`` (a list of
line dicts with ``y``, ``length``, ``color_rgb``, ``color_hex``,
``confidence`` etc.) plus the source image shape, and returns a structured
result splitting the input into kept vs rejected lines with explanations.

Pure: stdlib-only, no I/O, no OpenCV, no numpy, no network, no broker
imports. The CLI wrapper lives in ``tools/filter_fibo_lines.py``.

Filter rules (each can be disabled via FilterParams flags):

  A. Drop lines in the top/bottom UI bands of the screenshot.
  B. Drop low-value (dark) lines -- TradingView's default grid.
  C. Drop near-greyscale lines.
  D. Drop blue-dominant dark grid (TradingView's default theme).
  E. Score-time bonus for colours close to a palette of common Fibo
     stroke colours (Material green/orange/red/blue/yellow).

Lines that survive A-D are scored and sorted; the top-K are returned.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional


DEFAULT_PALETTE: tuple[str, ...] = (
    "#4CAF50",  # Material green
    "#FF9800",  # Material orange
    "#F44336",  # Material red
    "#2196F3",  # Material blue
    "#FFEB3B",  # Material yellow
)


@dataclass(frozen=True)
class FilterParams:
    margin_top: float = 0.05
    margin_bottom: float = 0.07
    min_value: float = 0.35
    min_chroma: int = 25
    blue_dark_grid: bool = True
    chroma_blue_bias: int = 10
    blue_dark_max_value_byte: int = 80
    palette: tuple[str, ...] = DEFAULT_PALETTE
    palette_bonus: float = 0.20
    palette_color_distance: int = 30
    top_k: int = 8

    def to_dict(self) -> dict:
        return {
            "margin_top": self.margin_top,
            "margin_bottom": self.margin_bottom,
            "min_value": self.min_value,
            "min_chroma": self.min_chroma,
            "blue_dark_grid": self.blue_dark_grid,
            "chroma_blue_bias": self.chroma_blue_bias,
            "blue_dark_max_value_byte": self.blue_dark_max_value_byte,
            "palette": list(self.palette),
            "palette_bonus": self.palette_bonus,
            "palette_color_distance": self.palette_color_distance,
            "top_k": self.top_k,
        }


@dataclass
class FilteredLine:
    y: int
    x_start: int
    x_end: int
    length: int
    color_bgr: list[int]
    color_rgb: list[int]
    color_hex: str
    angle_deg: float
    confidence: float
    score: float
    score_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "y": self.y,
            "x_start": self.x_start,
            "x_end": self.x_end,
            "length": self.length,
            "color_bgr": list(self.color_bgr),
            "color_rgb": list(self.color_rgb),
            "color_hex": self.color_hex,
            "angle_deg": self.angle_deg,
            "confidence": self.confidence,
            "score": round(self.score, 4),
            "score_breakdown": {k: round(v, 4) for k, v in self.score_breakdown.items()},
        }


@dataclass
class RejectedLine:
    y: int
    color_hex: str
    reason: str

    def to_dict(self) -> dict:
        return {"y": self.y, "color_hex": self.color_hex, "reason": self.reason}


@dataclass
class FilterResult:
    image_shape: tuple[int, int]
    params: FilterParams
    input_count: int
    kept: list[FilteredLine] = field(default_factory=list)
    rejected: list[RejectedLine] = field(default_factory=list)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    s = h.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"bad hex colour: {h!r}")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(int(x) - int(y)) for x, y in zip(a, b))


def _rgb_of(line: dict) -> tuple[int, int, int]:
    """Pull an (R, G, B) tuple out of a line dict.

    Prefers explicit ``color_rgb`` (detector emits it), falls back to
    parsing ``color_hex``, then to reversing ``color_bgr``.
    """
    if "color_rgb" in line and line["color_rgb"] is not None:
        r, g, b = line["color_rgb"]
        return int(r), int(g), int(b)
    if "color_hex" in line and line["color_hex"]:
        return _hex_to_rgb(line["color_hex"])
    if "color_bgr" in line and line["color_bgr"] is not None:
        b, g, r = line["color_bgr"]
        return int(r), int(g), int(b)
    raise ValueError("line is missing colour fields (color_rgb / color_hex / color_bgr)")


def _palette_bonus(
    rgb: tuple[int, int, int],
    palette: tuple[str, ...],
    max_distance: int,
    bonus: float,
) -> float:
    if bonus <= 0 or not palette:
        return 0.0
    for hex_color in palette:
        if _color_distance(rgb, _hex_to_rgb(hex_color)) <= max_distance:
            return bonus
    return 0.0


def filter_fibo_lines(
    lines: list[dict],
    image_shape: tuple[int, int],
    params: Optional[FilterParams] = None,
) -> FilterResult:
    """Split *lines* into kept / rejected per *params*; sort kept by score."""
    if params is None:
        params = FilterParams()
    h, w = image_shape
    if h <= 0 or w <= 0:
        raise ValueError(f"image_shape must be positive, got {image_shape!r}")

    result = FilterResult(
        image_shape=(int(h), int(w)),
        params=params,
        input_count=len(lines),
    )

    y_top_cut = params.margin_top * h
    y_bot_cut = (1.0 - params.margin_bottom) * h
    min_value_byte = params.min_value * 255.0

    for line in lines:
        y = int(line["y"])
        color_hex = line.get("color_hex") or "#000000"

        # Rule A: top / bottom UI bands.
        if y < y_top_cut:
            result.rejected.append(RejectedLine(
                y=y, color_hex=color_hex,
                reason=f"rule_a: y/H={y/h:.3f} < margin_top={params.margin_top}",
            ))
            continue
        if y > y_bot_cut:
            result.rejected.append(RejectedLine(
                y=y, color_hex=color_hex,
                reason=f"rule_a: y/H={y/h:.3f} > 1-margin_bottom={1-params.margin_bottom:.3f}",
            ))
            continue

        try:
            r, g, b = _rgb_of(line)
        except ValueError as exc:
            result.rejected.append(RejectedLine(y=y, color_hex=color_hex, reason=f"bad_colour: {exc}"))
            continue

        v_byte = max(r, g, b)
        chroma = max(r, g, b) - min(r, g, b)

        # Rule B: low value (dark grid).
        if v_byte < min_value_byte:
            result.rejected.append(RejectedLine(
                y=y, color_hex=color_hex,
                reason=f"rule_b: V={v_byte/255:.3f} < min_value={params.min_value}",
            ))
            continue

        # Rule C: near-greyscale.
        if chroma < params.min_chroma:
            result.rejected.append(RejectedLine(
                y=y, color_hex=color_hex,
                reason=f"rule_c: chroma={chroma} < min_chroma={params.min_chroma}",
            ))
            continue

        # Rule D: blue-dominant dark grid (TradingView dark theme default).
        if (
            params.blue_dark_grid
            and b > r + params.chroma_blue_bias
            and v_byte < params.blue_dark_max_value_byte
        ):
            result.rejected.append(RejectedLine(
                y=y, color_hex=color_hex,
                reason=(
                    f"rule_d: blue-dominant dark "
                    f"(B={b} > R={r}+{params.chroma_blue_bias}, V={v_byte}<{params.blue_dark_max_value_byte})"
                ),
            ))
            continue

        conf = float(line.get("confidence", 0.0))
        length = int(line.get("length", 0))
        length_ratio = length / max(1, w)

        bonus = _palette_bonus(
            (r, g, b),
            params.palette,
            params.palette_color_distance,
            params.palette_bonus,
        )

        breakdown = {
            "confidence": 0.5 * conf,
            "length": 0.3 * length_ratio,
            "chroma": 0.2 * (chroma / 255.0),
            "palette_bonus": bonus,
        }
        score = sum(breakdown.values())

        result.kept.append(FilteredLine(
            y=y,
            x_start=int(line.get("x_start", 0)),
            x_end=int(line.get("x_end", 0)),
            length=length,
            color_bgr=list(line.get("color_bgr", [b, g, r])),
            color_rgb=[r, g, b],
            color_hex=color_hex,
            angle_deg=float(line.get("angle_deg", 0.0)),
            confidence=conf,
            score=score,
            score_breakdown=breakdown,
        ))

    result.kept.sort(key=lambda L: L.score, reverse=True)
    if params.top_k > 0:
        result.kept = result.kept[: params.top_k]

    return result
