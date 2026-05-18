"""Linear pixel-y <-> price calibration for chart screenshots.

The Phase-5 CV pipeline produces Fibo line positions in pixel-y space
(the row index in the captured image). The Phase-5.5 quote feed
produces prices in instrument-price space (e.g. 2400.03 USD/oz for
gold). Bridging them needs a chart-coordinate transform.

This module implements the simplest reasonable transform: a two-point
linear interpolation. The operator inspects one screenshot, picks two
price labels that span most of the chart vertically, records their
pixel-y positions, and the rest is determined.

The transform stays in `vision/` because it is pure CV / geometry
logic, no I/O. It imports only stdlib + PyYAML, never any executor /
broker SDK / network library. (It is covered by the repo-wide guard
tests in test_no_hermes_yolo.py / test_mock_only.py.)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class CalibrationError(ValueError):
    """Raised when a calibration block is structurally invalid."""


@dataclass(frozen=True)
class ChartCalibration:
    """Two-point linear pixel-y <-> price transform.

    Screen y-coordinates grow *downward*, so ``pixel_y_high`` (a label
    near the top of the chart) must be numerically smaller than
    ``pixel_y_low``, and the price at ``pixel_y_high`` must be larger
    than the price at ``pixel_y_low``.
    """

    pixel_y_high: int   # nearer the top of the image, smaller y
    price_high: float
    pixel_y_low: int    # nearer the bottom of the image, larger y
    price_low: float

    def __post_init__(self) -> None:
        if not isinstance(self.pixel_y_high, int) or not isinstance(self.pixel_y_low, int):
            raise CalibrationError("pixel_y values must be int")
        if self.pixel_y_high >= self.pixel_y_low:
            raise CalibrationError(
                f"pixel_y_high ({self.pixel_y_high}) must be < pixel_y_low "
                f"({self.pixel_y_low}); screen y grows downward."
            )
        if self.price_high <= self.price_low:
            raise CalibrationError(
                f"price_high ({self.price_high}) must be > price_low ({self.price_low}); "
                "the label near the top of the chart represents the higher price."
            )

    @property
    def slope_price_per_pixel(self) -> float:
        """Always negative: as pixel-y grows (downward), price falls."""
        return (self.price_low - self.price_high) / (self.pixel_y_low - self.pixel_y_high)

    def pixel_y_to_price(self, pixel_y: float) -> float:
        return self.price_high + (pixel_y - self.pixel_y_high) * self.slope_price_per_pixel

    def price_to_pixel_y(self, price: float) -> float:
        return self.pixel_y_high + (price - self.price_high) / self.slope_price_per_pixel

    @classmethod
    def from_dict(cls, block: dict) -> "ChartCalibration":
        try:
            hi = block["reference_high"]
            lo = block["reference_low"]
            return cls(
                pixel_y_high=int(hi["pixel_y"]),
                price_high=float(hi["price"]),
                pixel_y_low=int(lo["pixel_y"]),
                price_low=float(lo["price"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CalibrationError(f"invalid calibration block: {exc}") from exc

    @classmethod
    def from_yaml(cls, path: str | Path) -> Optional["ChartCalibration"]:
        """Load calibration from a YAML file under the ``calibration:`` key.

        Returns ``None`` if the file exists but contains no
        ``calibration:`` block, so callers can cleanly fall back to a
        raw price-as-pixel-y interpretation. Raises
        :class:`CalibrationError` if the block is present but malformed,
        and ``FileNotFoundError`` if the file does not exist.
        """
        import yaml  # type: ignore

        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(str(p))
        with p.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        block = cfg.get("calibration") if isinstance(cfg, dict) else None
        if not block:
            return None
        return cls.from_dict(block)
