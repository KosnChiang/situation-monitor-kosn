"""Map raw CV-detected horizontal line segments to a named Fibonacci
retracement (:class:`vision.fibo_detector.FiboDetection`).

Why this exists
---------------
The Phase-5 CV pipeline (``vision.fibo_line_detector.detect_fibo_lines``
plus ``vision.fibo_line_filter.filter_fibo_lines``) returns
``FiboLineSegment`` objects that carry only y-coordinate, colour,
length, and a heuristic confidence -- they have **no semantic Fibo
level name** (``"0.618"``, ``"0.786"`` etc).

``strategy.fibo_mob_v2.FiboMobV2.evaluate(detection, last_price)``
requires a ``FiboDetection`` whose ``levels`` carry those exact
names. Without a bridge, the strategy class is dead code -- which is
exactly what the Phase-6 gap analysis found.

This module is that bridge. It is deliberately the **simplest
correct** heuristic ("position-order naming") with two extension
points (operator-supplied YOLO classifier; explicit calibration
projection planned for a later phase).

Strict mock-only:
  * pure function over already-detected lines, no I/O;
  * no broker SDK, no network, no executor;
  * the optional YOLO injection point takes a callable supplied by
    the caller -- this module never imports torch / ultralytics
    itself.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from vision.fibo_detector import FiboDetection, FiboLevel


CANONICAL_FIB_SEQUENCE_TOP_TO_BOTTOM = (
    "0.0", "0.382", "0.5", "0.618", "0.786", "1.0",
)
"""Canonical Fibonacci retracement labels, ordered top -> bottom.

Convention: ``0.0`` labels the line at the *top* of the chart (smallest
screen y), ``1.0`` labels the line at the *bottom* (largest screen y).
This matches the orientation that ``strategy.fibo_mob_v2.FiboMobV2``
assumes for its LONG decision: with ``entry=0.618``, ``stop=0.786``,
``target=0.0``, the strategy fires LONG when
``y(stop) > y(entry) > y(target)`` -- which holds when 0.786 is the
deeper retrace below 0.618 and 0.0 is the swing extreme at the top.

If the operator's TradingView Fib was drawn the *other* way round
(0% at the bottom), the strategy will instead fire SHORT against the
same set of lines. We have no way to distinguish "0% at top" from
"0% at bottom" from CV alone; a future enhancement could fall back to
the YOLO classifier or the colour of the rendered labels.

The strategy class uses ``"0.618"`` as the default entry level,
``"0.786"`` as stop, ``"0.0"`` as target.
"""

# Minimum lines required to produce a non-empty detection. Below this
# the strategy can never produce a confident long/short, so we return
# an empty detection (which FiboMobV2 turns into FLAT "no detection").
MIN_LINES_FOR_NAMING = 3


def _canonical_labels_for_n(n: int) -> list[str]:
    """Return the labels to assign to *n* lines, top -> bottom.

    Drops middle levels first so the strategy's entry/stop/target
    levels (``0.618`` / ``0.786`` / ``0.0``) survive as long as possible.
    """
    if n >= 6:
        return list(CANONICAL_FIB_SEQUENCE_TOP_TO_BOTTOM)
    if n == 5:
        # Drop 0.5 (the least informative middle level for MOB).
        return ["0.0", "0.382", "0.618", "0.786", "1.0"]
    if n == 4:
        # Drop 0.5 and 0.382. Keep the three the strategy actually
        # reads (entry/stop/target) plus the bottom anchor.
        return ["0.0", "0.618", "0.786", "1.0"]
    if n == 3:
        # Cannot include 0.786 here without dropping either an anchor
        # or 0.618 itself. Keep target, entry, and one anchor -- the
        # strategy will return FLAT ("missing fibo levels") because
        # 0.786 is absent, which is the correct conservative answer
        # when we only have three lines.
        return ["0.0", "0.618", "1.0"]
    return []


def _segment_view(seg: Any) -> dict:
    """Duck-typed accessor: works for both FiboLineSegment objects
    and the dict form emitted by ``tools.detect_fibo_lines`` JSON.
    """
    if isinstance(seg, dict):
        return {
            "y": int(seg["y"]),
            "x_start": int(seg.get("x_start", 0)),
            "x_end": int(seg.get("x_end", 0)),
            "confidence": float(seg.get("confidence", 0.0)),
        }
    return {
        "y": int(getattr(seg, "y")),
        "x_start": int(getattr(seg, "x_start", 0)),
        "x_end": int(getattr(seg, "x_end", 0)),
        "confidence": float(getattr(seg, "confidence", 0.0)),
    }


def name_levels(
    lines: Sequence[Any],
    *,
    image_shape: tuple[int, int] = (0, 0),
    yolo_classifier: Optional[Callable[[Any], Optional[FiboDetection]]] = None,
    yolo_image: Any = None,
) -> FiboDetection:
    """Convert a list of detected line segments to a FiboDetection.

    Parameters
    ----------
    lines:
        Iterable of either ``FiboLineSegment`` objects (from
        ``vision.fibo_line_detector``) or plain dicts (from the
        ``tools.detect_fibo_lines`` JSON sink). Duck-typed.
    image_shape:
        ``(height, width)`` of the source image. Propagated to the
        returned ``FiboDetection`` so downstream consumers can scale
        coordinates if needed.
    yolo_classifier:
        Optional callable ``(image) -> Optional[FiboDetection]``. If
        supplied, called once with ``yolo_image``; if it returns a
        non-None detection whose ``.ok`` is True, that detection is
        returned verbatim and the position-order fallback is skipped.
        Allows the operator to plug in YOLO without this module
        importing torch / ultralytics directly.
    yolo_image:
        Image to pass to ``yolo_classifier``. Ignored if
        ``yolo_classifier`` is None.

    Returns
    -------
    FiboDetection
        A detection with semantic level names. ``.ok`` is False when
        fewer than ``MIN_LINES_FOR_NAMING`` lines were provided and
        the YOLO path did not produce a usable detection.
    """
    if yolo_classifier is not None and yolo_image is not None:
        try:
            yolo_result = yolo_classifier(yolo_image)
        except Exception:
            yolo_result = None
        if yolo_result is not None and yolo_result.ok:
            return yolo_result

    views = [_segment_view(s) for s in lines]
    views.sort(key=lambda v: v["y"])
    n = len(views)
    if n < MIN_LINES_FOR_NAMING:
        return FiboDetection(image_shape=image_shape, levels=[])

    labels = _canonical_labels_for_n(n)
    if not labels:
        return FiboDetection(image_shape=image_shape, levels=[])

    levels = []
    for view, label in zip(views, labels):
        levels.append(
            FiboLevel(
                name=label,
                y_pixel=float(view["y"]),
                confidence=float(view["confidence"]),
                bbox=(
                    float(view["x_start"]),
                    float(view["y"] - 1),
                    float(view["x_end"]),
                    float(view["y"] + 1),
                ),
            )
        )
    return FiboDetection(image_shape=image_shape, levels=levels)
