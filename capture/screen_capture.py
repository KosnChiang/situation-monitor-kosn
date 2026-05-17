"""Screen capture via mss. Returns BGR numpy arrays.

mss/numpy are imported lazily so the rest of the pipeline (and tests)
can run on machines where the native deps are not installed yet.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CaptureRegion:
    monitor_index: int = 1
    left: int | None = None
    top: int | None = None
    width: int | None = None
    height: int | None = None


class ScreenCapture:
    def __init__(self, region: CaptureRegion | None = None) -> None:
        self.region = region or CaptureRegion()
        self._sct: Any = None

    def _ensure(self) -> None:
        if self._sct is None:
            import mss  # type: ignore

            self._sct = mss.mss()

    def grab(self):
        """Capture a frame and return a BGR ndarray (H, W, 3)."""
        import numpy as np  # type: ignore

        self._ensure()
        mons = self._sct.monitors
        idx = max(0, min(self.region.monitor_index, len(mons) - 1))
        mon = dict(mons[idx])
        if self.region.left is not None:
            mon["left"] = self.region.left
        if self.region.top is not None:
            mon["top"] = self.region.top
        if self.region.width is not None:
            mon["width"] = self.region.width
        if self.region.height is not None:
            mon["height"] = self.region.height

        raw = self._sct.grab(mon)
        arr = np.array(raw)  # BGRA
        return arr[:, :, :3]  # drop alpha -> BGR

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None
