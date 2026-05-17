"""Generate mock trading signals from detected Fibo lines + a mock price.

Reads ``logs/fibo_lines.json`` (produced by ``tools.detect_fibo_lines``),
takes a mock price expressed in image y-pixel space (same coordinate
system as the lines), and emits a JSON signal record. If
``--submit`` is set and the signal is not FLAT, the signal is also
piped through ``risk.risk_gate.RiskGate`` and ``executor.mock_executor.MockExecutor``
so the trade log mirrors what the live pipeline would record.

Strictly mock-only:
  * imports only stdlib + this repo's modules; no network library;
  * never reads or sends a broker credential;
  * MockExecutor's own boundary check refuses to write to
    ``logs/trades.jsonl`` unless ``LIVE_TRADING=false`` and
    ``EXECUTION_MODE=mock``.

Run:
    python -m tools.mock_fibo_signal --price-y 450 --symbol XAUUSD
    python -m tools.mock_fibo_signal --price-y 450 --tolerance-px 8 --submit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_FIBO_PATH = "logs/fibo_lines.json"
DEFAULT_SIGNALS_PATH = "logs/signals.jsonl"


def _now() -> tuple[float, str]:
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    return ts, iso


def _signal_record(
    *,
    symbol: str,
    side: str,
    reason: str,
    fibo_line_y: Optional[int],
    confidence: float,
) -> dict:
    ts, iso = _now()
    return {
        "ts": ts,
        "timestamp": iso,
        "symbol": symbol,
        "side": side,
        "reason": reason,
        "fibo_line_y": fibo_line_y,
        "confidence": float(confidence),
        "mode": "mock",
    }


def _nearest_line(lines: list[dict], price_y: float, tolerance_px: int):
    if not lines:
        return None, None
    nearest = min(lines, key=lambda line: abs(line["y"] - price_y))
    distance = abs(nearest["y"] - price_y)
    if distance <= tolerance_px:
        return nearest, distance
    return None, distance


def generate_signal(
    *,
    fibo_lines: list[dict],
    price_y: float,
    symbol: str,
    tolerance_px: int,
    min_confidence: float,
) -> dict:
    """Pure function: lines + price -> signal dict. No I/O, no networking."""
    hit, dist = _nearest_line(fibo_lines, price_y, tolerance_px)
    if hit is None:
        return _signal_record(
            symbol=symbol,
            side="FLAT",
            reason=f"no fibo line within {tolerance_px}px of price_y={price_y}",
            fibo_line_y=None,
            confidence=0.0,
        )

    conf = float(hit.get("confidence", 0.0))
    if conf < min_confidence:
        return _signal_record(
            symbol=symbol,
            side="FLAT",
            reason=f"fibo touch at y={hit['y']} but confidence {conf:.2f} < {min_confidence}",
            fibo_line_y=int(hit["y"]),
            confidence=conf,
        )

    # MVP convention: a clean Fibo touch is a LONG bounce. The point of
    # this layer is to wire the plumbing, not to backtest a thesis.
    return _signal_record(
        symbol=symbol,
        side="LONG",
        reason=f"fibo touch y={hit['y']} dist={dist}px",
        fibo_line_y=int(hit["y"]),
        confidence=conf,
    )


def _append_signal(signal: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(signal, ensure_ascii=False) + "\n")


def _submit_through_executor(signal: dict, price_y: float) -> Optional[dict]:
    """Pipe an approved non-FLAT signal through RiskGate + MockExecutor.

    Returns the fill dict if the executor wrote a row, otherwise None.
    """
    if signal["side"] == "FLAT":
        return None

    from risk.risk_gate import RiskGate
    from executor.mock_executor import MockExecutor
    from strategy.fibo_mob_v2 import Signal as StrategySignal

    gate = RiskGate(min_confidence=signal["confidence"])

    strategy_sig = StrategySignal(
        side=signal["side"],
        entry=float(price_y),
        stop=float(price_y) + 20.0,
        target=float(price_y) - 40.0,
        confidence=float(signal["confidence"]),
        reason=signal["reason"],
    )
    decision = gate.check(strategy_sig)
    if not decision.approved:
        return {"submitted": False, "reason": decision.reason}

    executor = MockExecutor()
    fill = executor.submit(strategy_sig)
    return {
        "submitted": True,
        "mode": fill.mode,
        "side": fill.side,
        "entry": fill.entry,
        "stop": fill.stop,
        "target": fill.target,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fibo-lines", default=DEFAULT_FIBO_PATH)
    ap.add_argument("--out-signals", default=DEFAULT_SIGNALS_PATH)
    ap.add_argument("--symbol", default="MOCK")
    ap.add_argument(
        "--price-y",
        type=float,
        required=True,
        help="mock price expressed in image y-pixel space (same units as the detected lines)",
    )
    ap.add_argument("--tolerance-px", type=int, default=8)
    ap.add_argument("--min-confidence", type=float, default=0.55)
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout instead of summary text")
    ap.add_argument(
        "--submit",
        action="store_true",
        help="pipe an approved non-FLAT signal through RiskGate + MockExecutor "
             "so a row lands in logs/trades.jsonl",
    )
    args = ap.parse_args()

    fibo_path = Path(args.fibo_lines)
    if not fibo_path.exists():
        print(f"FAIL: fibo lines file not found: {fibo_path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(fibo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: {fibo_path} is not valid JSON: {exc}", file=sys.stderr)
        return 3

    lines = data.get("lines", []) if isinstance(data, dict) else []

    signal = generate_signal(
        fibo_lines=lines,
        price_y=args.price_y,
        symbol=args.symbol,
        tolerance_px=args.tolerance_px,
        min_confidence=args.min_confidence,
    )

    out_signals = Path(args.out_signals)
    _append_signal(signal, out_signals)

    fill = None
    if args.submit:
        fill = _submit_through_executor(signal, args.price_y)

    if args.json:
        print(json.dumps({"signal": signal, "fill": fill}, ensure_ascii=False))
    else:
        print(
            f"signal: {signal['side']:5}  symbol={signal['symbol']}  "
            f"fibo_y={signal['fibo_line_y']}  conf={signal['confidence']:.2f}"
        )
        print(f"reason: {signal['reason']}")
        print(f"wrote   -> {out_signals}")
        if fill and fill.get("submitted"):
            print(f"submitted via MockExecutor -> logs/trades.jsonl (mode={fill['mode']})")
        elif fill is not None:
            print(f"not submitted: {fill.get('reason', 'rejected by risk gate')}")
        elif args.submit:
            print("not submitted: signal is FLAT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
