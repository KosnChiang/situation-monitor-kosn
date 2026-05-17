"""Horizontal Fibonacci-line detector built on OpenCV.

Pure CV pipeline — no torch, no broker SDKs, no network. The Ultralytics
YOLO detector in `vision/fibo_detector.py` is the eventual target, but
this module is the deterministic baseline for the MVP: load a chart
screenshot, find the horizontal coloured lines that mark Fibo levels,
and return their y position, x extent, and dominant colour.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FiboLineSegment:
    y: int                              # average y position (pixels)
    x_start: int
    x_end: int
    length: int                         # x_end - x_start, pixels
    color_bgr: tuple[int, int, int]     # median BGR colour along the line
    angle_deg: float                    # signed angle off horizontal

    @property
    def color_hex(self) -> str:
        b, g, r = self.color_bgr
        return f"#{r:02X}{g:02X}{b:02X}"


@dataclass
class FiboLinesResult:
    lines: list[FiboLineSegment] = field(default_factory=list)
    image_shape: tuple[int, int] = (0, 0)  # (h, w)


def detect_fibo_lines(
    img,
    min_length_ratio: float = 0.3,
    angle_tolerance_deg: float = 2.0,
    cluster_y_px: int = 4,
    canny_lo: int = 50,
    canny_hi: int = 150,
    hough_threshold: int = 80,
    max_line_gap: int = 20,
) -> FiboLinesResult:
    """Detect near-horizontal line segments in a BGR image.

    Steps:
      1. Greyscale + Canny edges.
      2. Probabilistic Hough transform with a min length proportional
         to image width.
      3. Drop anything tilted more than ``angle_tolerance_deg`` from
         horizontal.
      4. Cluster surviving segments whose y-coordinates are within
         ``cluster_y_px`` of each other — Hough often returns several
         broken pieces along the same chart line.
      5. For each cluster sample the dominant colour as the median BGR
         of a 3-row strip along the segment.
    """
    import cv2  # type: ignore
    import numpy as np  # type: ignore

    if img is None or img.ndim != 3 or img.shape[2] != 3:
        raise ValueError("detect_fibo_lines expects a BGR image (H, W, 3)")

    h, w = img.shape[:2]
    min_length_px = max(10, int(w * min_length_ratio))

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, canny_lo, canny_hi)
    raw = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=hough_threshold,
        minLineLength=min_length_px,
        maxLineGap=max_line_gap,
    )

    candidates: list[tuple[int, int, int, float]] = []
    if raw is not None:
        for ln in raw:
            x1, y1, x2, y2 = (int(v) for v in ln[0])
            dx = x2 - x1
            dy = y2 - y1
            if dx == 0:
                continue
            angle = float(np.degrees(np.arctan2(dy, dx)))
            if abs(angle) > angle_tolerance_deg:
                continue
            candidates.append(((y1 + y2) // 2, min(x1, x2), max(x1, x2), angle))

    candidates.sort(key=lambda c: c[0])
    clusters: list[list[tuple[int, int, int, float]]] = []
    for c in candidates:
        if clusters and abs(c[0] - clusters[-1][-1][0]) <= cluster_y_px:
            clusters[-1].append(c)
        else:
            clusters.append([c])

    result = FiboLinesResult(image_shape=(h, w))
    for cluster in clusters:
        y_avg = int(np.mean([c[0] for c in cluster]))
        xs_start = min(c[1] for c in cluster)
        xs_end = max(c[2] for c in cluster)
        length = xs_end - xs_start
        if length < min_length_px:
            continue

        y0 = max(0, y_avg - 1)
        y1 = min(h, y_avg + 2)
        strip = img[y0:y1, xs_start:xs_end].reshape(-1, 3)
        med = np.median(strip, axis=0)
        color_bgr = (int(med[0]), int(med[1]), int(med[2]))

        result.lines.append(
            FiboLineSegment(
                y=y_avg,
                x_start=xs_start,
                x_end=xs_end,
                length=length,
                color_bgr=color_bgr,
                angle_deg=float(np.mean([c[3] for c in cluster])),
            )
        )

    result.lines.sort(key=lambda s: s.y)
    return result


def draw_debug(img, result: FiboLinesResult):
    """Return a copy of ``img`` with detected lines + labels overlaid."""
    import cv2  # type: ignore

    out = img.copy()
    for seg in result.lines:
        cv2.line(out, (seg.x_start, seg.y), (seg.x_end, seg.y), (0, 255, 0), 2)
        label = f"y={seg.y} len={seg.length} {seg.color_hex}"
        cv2.putText(
            out,
            label,
            (seg.x_start + 5, max(12, seg.y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0),
            1,
            cv2.LINE_AA,
        )
    return out
