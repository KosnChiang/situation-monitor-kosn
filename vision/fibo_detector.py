"""Fibonacci-level detector built on top of an Ultralytics YOLO model.

The torch / ultralytics imports are deferred so unit tests do not require
a GPU build. Returns a list of detected fibo levels with normalized prices.

``FiboDetector.detect`` is the strict API: it raises when torch /
ultralytics / weights are unavailable. For pipeline integration (where
the namer in ``vision.fibo_line_level_namer`` wants to fall back to
position-order naming when YOLO is unavailable) prefer
``FiboDetector.try_detect`` -- it returns ``None`` instead of raising
when any prerequisite is missing.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class FiboLevel:
    name: str          # "0.382", "0.5", "0.618", "0.786", "1.0", "0.0"
    y_pixel: float     # y position in the captured image
    confidence: float
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


@dataclass
class FiboDetection:
    levels: list[FiboLevel] = field(default_factory=list)
    image_shape: tuple[int, int] = (0, 0)  # (h, w)

    @property
    def ok(self) -> bool:
        return len(self.levels) >= 2


class FiboDetector:
    def __init__(self, weights: str | None = None, device: str | None = None) -> None:
        self.weights = weights or os.getenv("FIBO_WEIGHTS", "models/fibo.pt")
        # On a dual-3090 box CUDA_VISIBLE_DEVICES=1 maps the chosen card to cuda:0.
        self.device = device or os.getenv("FIBO_DEVICE", "cuda:0")
        self._model: Any = None

    def _ensure(self) -> None:
        if self._model is not None:
            return
        from ultralytics import YOLO  # type: ignore

        self._model = YOLO(self.weights)

    def detect(self, frame) -> FiboDetection:
        """Run detection on a BGR frame. Returns FiboDetection.

        Raises ``ImportError`` if ultralytics is not installed,
        ``FileNotFoundError`` if the weights file is missing, and any
        runtime error from the YOLO call. Use :meth:`try_detect` for
        a non-raising variant suitable for pipeline integration.
        """
        self._ensure()
        results = self._model.predict(frame, device=self.device, verbose=False)
        det = FiboDetection(image_shape=(frame.shape[0], frame.shape[1]))
        if not results:
            return det
        r = results[0]
        names = getattr(r, "names", {}) or {}
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            return det
        for b in boxes:
            cls_id = int(b.cls.item()) if hasattr(b.cls, "item") else int(b.cls)
            conf = float(b.conf.item()) if hasattr(b.conf, "item") else float(b.conf)
            x1, y1, x2, y2 = (float(v) for v in b.xyxy[0].tolist())
            det.levels.append(
                FiboLevel(
                    name=str(names.get(cls_id, cls_id)),
                    y_pixel=(y1 + y2) / 2.0,
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                )
            )
        return det

    def try_detect(self, frame) -> Optional[FiboDetection]:
        """Best-effort detection. Returns ``None`` instead of raising
        when any prerequisite is missing.

        Returns None if:
          * ``torch`` or ``ultralytics`` cannot be imported (CPU-only
            box, or deps not installed yet);
          * the weights file at ``self.weights`` does not exist;
          * the YOLO call itself errors out (corrupted weights,
            CUDA OOM, etc.).

        Returns the FiboDetection in all other cases (including a
        detection with zero levels -- the namer caller will fall back
        to position-order naming when ``.ok`` is False).

        This is the pipeline-friendly entry point used by
        ``vision.fibo_line_level_namer.name_levels`` via the
        ``yolo_classifier`` parameter.
        """
        try:
            import torch  # noqa: F401
            import ultralytics  # noqa: F401
        except ImportError:
            return None

        if not Path(self.weights).exists():
            return None

        try:
            return self.detect(frame)
        except Exception:
            return None
