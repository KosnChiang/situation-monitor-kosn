"""Fibo MOB v2 strategy.

Given a FiboDetection plus the latest price, decide whether to emit a
LONG / SHORT / FLAT signal. Pure function over the detection — no I/O.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vision.fibo_detector import FiboDetection


Side = Literal["LONG", "SHORT", "FLAT"]


@dataclass
class Signal:
    side: Side
    entry: float
    stop: float
    target: float
    confidence: float
    reason: str


@dataclass
class FiboMobV2:
    # MOB = "Mitigation Order Block" zone around the 0.618 retrace.
    entry_level: str = "0.618"
    stop_level: str = "0.786"
    target_level: str = "0.0"
    min_confidence: float = 0.55

    def evaluate(self, detection: FiboDetection, last_price: float) -> Signal:
        if not detection.ok:
            return Signal("FLAT", last_price, last_price, last_price, 0.0, "no detection")

        by_name = {lvl.name: lvl for lvl in detection.levels}
        if not all(k in by_name for k in (self.entry_level, self.stop_level, self.target_level)):
            return Signal("FLAT", last_price, last_price, last_price, 0.0, "missing fibo levels")

        entry = by_name[self.entry_level].y_pixel
        stop = by_name[self.stop_level].y_pixel
        target = by_name[self.target_level].y_pixel
        conf = min(
            by_name[self.entry_level].confidence,
            by_name[self.stop_level].confidence,
            by_name[self.target_level].confidence,
        )
        if conf < self.min_confidence:
            return Signal("FLAT", last_price, last_price, last_price, conf, "low confidence")

        # In screen coords y grows downward, so stop above entry => short setup, etc.
        if stop > entry > target:
            side: Side = "LONG"
        elif stop < entry < target:
            side = "SHORT"
        else:
            return Signal("FLAT", last_price, last_price, last_price, conf, "levels not ordered")

        return Signal(side, last_price, last_price, last_price, conf, f"fibo-mob {self.entry_level}")
