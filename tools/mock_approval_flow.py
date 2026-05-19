"""Mock approval-flow CLI (Phase 6.A-1, dry-run).

Scripts one end-to-end approval cycle: build a synthetic candidate
trade, push it through ``approval.approval_gate.ApprovalGate``, and
simulate the operator's Telegram click in code. The real Telegram
bot is NOT involved in this phase.

Usage:
    python -m tools.mock_approval_flow --action approve --chat-id 12345
    python -m tools.mock_approval_flow --action reject  --chat-id 12345
    python -m tools.mock_approval_flow --action timeout --timeout 0.2
    python -m tools.mock_approval_flow --action unauthorized --chat-id 99999

Strictly mock-only:
  * Imports only stdlib + ``approval.*``.
  * Never sends an HTTP request.
  * ApprovalGate refuses to construct if ``LIVE_TRADING`` is not ``false``.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from approval.approval_gate import ApprovalGate, LiveTradingForbidden  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mock approval flow (dry-run).")
    p.add_argument(
        "--action",
        choices=["approve", "reject", "timeout", "unauthorized"],
        required=True,
        help="What operator behaviour to simulate.",
    )
    p.add_argument("--chat-id", default="", help="Simulated operator chat id.")
    p.add_argument(
        "--allowed-ids",
        default=os.getenv("TELEGRAM_APPROVE_CHAT_IDS", "12345"),
        help="Comma-separated allowlist (default: 12345 if env empty).",
    )
    p.add_argument("--timeout", type=float, default=2.0, help="Approval timeout in seconds.")
    p.add_argument("--submit-delay", type=float, default=0.0,
                   help="Sleep N seconds before the simulated callback fires.")
    p.add_argument("--symbol", default="MOCK")
    p.add_argument("--side", default="LONG", choices=["LONG", "SHORT", "FLAT"])
    p.add_argument("--entry", type=float, default=100.0)
    p.add_argument("--stop", type=float, default=99.0)
    p.add_argument("--target", type=float, default=110.0)
    p.add_argument("--confidence", type=float, default=0.8)
    p.add_argument("--qty", type=float, default=1.0)
    p.add_argument("--reason", default="mock-approval-flow")
    p.add_argument("--log-path", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    allowed = [s.strip() for s in args.allowed_ids.split(",") if s.strip()]
    try:
        gate = ApprovalGate(
            timeout_seconds=args.timeout,
            allowed_chat_ids=allowed,
            log_path=args.log_path,
        )
    except LiveTradingForbidden as e:
        print(f"ABORT: {e}", file=sys.stderr)
        return 2

    req = gate.request(
        symbol=args.symbol,
        side=args.side,
        entry=args.entry,
        stop=args.stop,
        target=args.target,
        confidence=args.confidence,
        qty=args.qty,
        reason=args.reason,
    )
    print(
        f"[request] id={req.request_id} side={req.side} symbol={req.symbol} "
        f"entry={req.entry} timeout={req.timeout_seconds}s"
    )

    if args.action == "timeout":
        decision = gate.await_decision(req)
    else:
        if args.action == "unauthorized":
            sender_id = args.chat_id or "999999"
            approve_flag = True
        elif args.action == "approve":
            sender_id = args.chat_id or (allowed[0] if allowed else "")
            approve_flag = True
        else:
            sender_id = args.chat_id or (allowed[0] if allowed else "")
            approve_flag = False

        def _submit() -> None:
            import time as _t

            if args.submit_delay > 0:
                _t.sleep(args.submit_delay)
            gate.submit_decision(
                req.request_id, approve=approve_flag, operator_chat_id=sender_id
            )

        t = threading.Thread(target=_submit, daemon=True)
        t.start()
        decision = gate.await_decision(req)
        t.join(timeout=1.0)

    print(
        f"[decision] approved={decision.approved} reason={decision.reason} "
        f"operator={decision.operator_chat_id} elapsed_ms={decision.elapsed_ms:.1f}"
    )
    print(f"[log] {gate.log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
