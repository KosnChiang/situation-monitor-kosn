"""CLI: dry-run a live order end-to-end through LivePipeline.

Reads an AI decision JSON from a file or stdin, runs it through
ai_decision.validator -> LiveUnlockGate -> KillSwitch ->
tradable check -> MicroLiveGate -> FakeLiveBrokerAdapter, and prints
a human or machine-readable summary. Exit code reflects which step
the pipeline reached.

Exit codes:
  0  order filled
  2  ai_decision invalid (also: file not found, bad JSON)
  3  LiveUnlockGate refused
  4  KillSwitch active
  5  MicroLiveGate rejected
  6  adapter error
  7  ai_decision valid but not tradable (FLAT / WATCH / low confidence)

Usage:
    python -m tools.dry_run_live_order --ai-decision decision.json
    cat decision.json | python -m tools.dry_run_live_order --ai-decision -
    python -m tools.dry_run_live_order --ai-decision decision.json --json

Mock-only:
  * Imports only the in-repo fake adapter.
  * No broker SDK, no outbound HTTP, no broker credential env read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from live.fake_live_adapter import FakeLiveBrokerAdapter  # noqa: E402
from live.kill_switch import KillSwitch  # noqa: E402
from live.live_unlock_gate import LiveUnlockGate  # noqa: E402
from live.micro_live_gate import MicroLiveGate  # noqa: E402
from live.pipeline import LivePipeline, PipelineResult  # noqa: E402


def _read_input(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"AI decision file not found: {source}")
    return path.read_text(encoding="utf-8")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Dry-run a live order through the Phase 6.B-2 pipeline.",
    )
    ap.add_argument(
        "--ai-decision",
        required=True,
        help="Path to a JSON file containing the AI decision, or '-' for stdin.",
    )
    ap.add_argument("--qty", type=float, default=1.0)
    ap.add_argument("--daily-trade-count", type=int, default=0)
    ap.add_argument("--daily-loss", type=float, default=0.0)
    ap.add_argument("--min-trade-confidence", type=float, default=0.7)
    ap.add_argument(
        "--operator-equity",
        type=float,
        default=None,
        help="Equity for the fake adapter's account_equity() return value.",
    )
    ap.add_argument(
        "--kill-file",
        default=None,
        help="Path to the kill-switch sentinel file (default: logs/.killswitch).",
    )
    ap.add_argument("--ai-decisions-log", default=None)
    ap.add_argument("--orders-log", default=None)
    ap.add_argument("--fills-log", default=None)
    ap.add_argument("--rejections-log", default=None)
    ap.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable JSON summary to stdout instead of text.",
    )
    return ap.parse_args(argv)


def _print_result(result: PipelineResult, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.summary(), ensure_ascii=False))
        return
    print(f"outcome      : {result.outcome}")
    print(f"exit_code    : {result.exit_code}")
    print(f"decision_id  : {result.decision_id}")
    print(f"order_id     : {result.order_id}")
    if result.fill is not None:
        print(f"fill_price   : {result.fill.fill_price}")
        print(f"fill_qty     : {result.fill.qty}")
        print(f"fill_status  : {result.fill.status}")
    if result.rejection_reason:
        print(f"rejected     : {result.rejection_reason}")


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        raw_text = _read_input(args.ai_decision)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"ERROR: cannot read input: {e}", file=sys.stderr)
        return 2

    kill_file = args.kill_file or "logs/.killswitch"
    fake_equity_kwargs: dict = {}
    if args.operator_equity is not None:
        fake_equity_kwargs["account_equity"] = args.operator_equity

    adapter = FakeLiveBrokerAdapter(
        order_log_path=args.orders_log,
        fill_log_path=args.fills_log,
        rejection_log_path=args.rejections_log,
        **fake_equity_kwargs,
    )
    kill_switch = KillSwitch(kill_file_path=kill_file)
    unlock_gate = LiveUnlockGate(kill_file_path=kill_file)
    micro_gate = MicroLiveGate(rejection_log_path=args.rejections_log)

    pipeline = LivePipeline(
        adapter=adapter,
        kill_switch=kill_switch,
        unlock_gate=unlock_gate,
        micro_gate=micro_gate,
        min_trade_confidence=args.min_trade_confidence,
        ai_decisions_log=args.ai_decisions_log,
        rejections_log=args.rejections_log,
    )

    result = pipeline.run(
        raw_text,
        qty=args.qty,
        daily_trade_count=args.daily_trade_count,
        daily_loss=args.daily_loss,
    )

    _print_result(result, json_output=args.json)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
