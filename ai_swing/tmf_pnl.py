"""TMF (微型台指) P&L math (Phase 7.A).

Point value: NT$10 / point / contract.
"""
from __future__ import annotations


POINT_VALUE_TWD = 10.0
SLIPPAGE_BUFFER_POINTS = 2.0


def unrealized_points(side: str, entry: float, current_price: float) -> float:
    if side == "LONG":
        return float(current_price) - float(entry)
    if side == "SHORT":
        return float(entry) - float(current_price)
    return 0.0


def unrealized_twd(side: str, entry: float, current_price: float,
                  qty: float = 1.0, point_value: float = POINT_VALUE_TWD) -> float:
    return unrealized_points(side, entry, current_price) * float(qty) * float(point_value)


def risk_twd(entry: float, stop: float, qty: float = 1.0,
            point_value: float = POINT_VALUE_TWD) -> float:
    return abs(float(entry) - float(stop)) * float(qty) * float(point_value)


def protected_profit_twd(side: str, entry: float, current_stop: float,
                         qty: float = 1.0,
                         point_value: float = POINT_VALUE_TWD,
                         slippage_buffer: float = SLIPPAGE_BUFFER_POINTS) -> float:
    if side == "LONG":
        protected_points = float(current_stop) - float(entry) - float(slippage_buffer)
    elif side == "SHORT":
        protected_points = float(entry) - float(current_stop) - float(slippage_buffer)
    else:
        return 0.0
    return max(0.0, protected_points * float(qty) * float(point_value))


def r_multiple(side: str, entry: float, current_price: float,
              initial_stop: float) -> float:
    initial_risk = abs(float(entry) - float(initial_stop))
    if initial_risk == 0:
        return 0.0
    return unrealized_points(side, entry, current_price) / initial_risk
