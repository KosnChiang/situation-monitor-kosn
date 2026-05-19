"""run_ai_swing_once.py -- one-shot AI swing decision CLI (Phase 7.A).

Reads a ChartContext JSON (and optional position / cooldown state),
runs the deterministic engine, writes the decision to
``logs/ai_swing_decisions.jsonl``, optionally dispatches an ENTRY to
the in-repo fake adapter, and prints a summary.

This is the manual counterpart to ``app/webhook_ai_swing.py``. Useful
for offline replay or testing without running a webhook server.

Usage::

    python -m tools.run_ai_swing_once --chart-context ctx.json --json
    cat ctx.json | python -m tools.run_ai_swing_once --chart-context - --json

Mock-only: no broker SDK, no outbound HTTP, no broker credential read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_swing.context import build_chart_context_from_tv_alert
from ai_swing.engine import CooldownState, PositionState, decide
from ai_swing.logging import write_ai_swing_decision, write_chart_context


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run one AI-swing decision.")
    ap.add_argument(
        "--chart-context", required=True,
        help="Path to chart-context JSON, or '-' for stdin.",
    )
    ap.add_argument("--position", default=None,
                    help="Optional path to position-state JSON.")
    ap.add_argument("--cooldown", default=None,
                    help="Optional path to cooldown-state JSON.")
    ap.add_argument("--mode", default="paper", choices=("paper", "fake-live"))
    ap.add_argument("--auto-submit", action="store_true",
                    help="Dispatch ENTRY decisions via FakeLiveBrokerAdapter.")
    ap.add_argument("--json", action="store_true")
    return ap.parse_args(argv)


def _read_source(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    p = Path(source)
    if not p.exists():
        raise FileNotFoundError(f"input not found: {source}")
    return p.read_text(encoding="utf-8")


def _load_position(path: Optional[str]) -> Optional[PositionState]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not data:
        return None
    return PositionState(
        side=str(data["side"]),
        entry=float(data["entry"]),
        qty=float(data.get("qty", 1.0)),
        open_order_id=str(data.get("open_order_id", "")),
        initial_stop=float(data.get("initial_stop", 0.0)),
        current_stop=(float(data["current_stop"])
                      if data.get("current_stop") is not None else None),
        bars_held=int(data.get("bars_held", 0)),
    )


def _load_cooldown(path: Optional[str]) -> Optional[CooldownState]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    if not data or "last_ts" not in data:
        return None
    return CooldownState(
        last_action=str(data.get("last_action", "")),
        last_ts=float(data["last_ts"]),
        cooldown_seconds=float(data.get("cooldown_seconds", 300.0)),
    )


def _dispatch_entry_fake(decision) -> dict:
    """Submit ENTRY via fake adapter. Returns {order_id, fill_price}."""
    from live.fake_live_adapter import FakeLiveBrokerAdapter
    from live.models import LiveOrder

    adapter = FakeLiveBrokerAdapter()
    adapter.connect()
    try:
        ts = time.time()
        iso = datetime.now(timezone.utc).isoformat()
        order = LiveOrder(
            order_id=str(uuid.uuid4()),
            ts=ts,
            timestamp=iso,
            decision_id=decision.decision_id,
            symbol=decision.symbol,
            side=decision.side,
            qty=float(decision.qty),
            entry=float(decision.entry),
            stop=float(decision.stop),
            target=float(decision.target),
            confidence=float(decision.confidence),
            reason=decision.reason,
        )
        fill = adapter.submit_order(order)
        return {"order_id": order.order_id, "fill_price": fill.fill_price}
    finally:
        adapter.disconnect()


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        raw = _read_source(args.chart_context)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    try:
        alert = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 2

    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    context_id = str(uuid.uuid4())

    try:
        ctx = build_chart_context_from_tv_alert(
            alert, context_id=context_id, ts=ts, timestamp=iso,
        )
    except (KeyError, TypeError, ValueError) as e:
        print(f"ERROR: invalid chart context: {e}", file=sys.stderr)
        return 2

    write_chart_context(ctx)

    position = _load_position(args.position)
    cooldown = _load_cooldown(args.cooldown)

    decision = decide(ctx=ctx, position=position, cooldown=cooldown, mode=args.mode)
    write_ai_swing_decision(decision)

    submitted = False
    order_id = None
    fill_price = None
    if args.auto_submit and decision.action == "ENTRY":
        try:
            res = _dispatch_entry_fake(decision)
            order_id = res["order_id"]
            fill_price = res["fill_price"]
            submitted = True
        except Exception as e:
            print(f"ERROR: dispatch failed: {e}", file=sys.stderr)

    summary = {
        "context_id": context_id,
        "decision_id": decision.decision_id,
        "action": decision.action,
        "side": decision.side,
        "confidence": decision.confidence,
        "reason": decision.reason,
        "submitted": submitted,
        "order_id": order_id,
        "fill_price": fill_price,
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        for k, v in summary.items():
            print(f"{k:14}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
