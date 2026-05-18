"""Standalone read-only quote feeder.

Spawns a quote provider, fetches one quote per ``--interval``
seconds, and appends each as a single JSON line to ``--output``
(default ``logs/quotes.jsonl``). Designed to run as its own
process; the order-execution side (``tools/mock_fibo_signal.py``
with ``--submit``) must run in a separate process and consume the
JSONL file as the only IPC channel.

Strictly read-only:
  * imports only stdlib + ``quote.quote_provider``;
  * no ``executor.*`` / ``risk.*`` / ``strategy.*`` imports;
  * no broker SDK reference (enforced by tests);
  * no order-placement function names (submit / place_order /
    send_order / execute_live);
  * refuses to start unless ``LIVE_TRADING=false`` and the chosen
    provider's own ``__init__`` accepts the env.

Run:
    python -m tools.quote_feed --symbol XAUUSD --provider mock --once
    python -m tools.quote_feed --symbol XAUUSD --provider mock --interval 2 --max-iterations 30
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from quote.quote_provider import PROVIDERS, QuoteFeedForbidden


def _refuse_if_live() -> None:
    live = (os.getenv("LIVE_TRADING", "false") or "").strip().lower()
    if live != "false":
        raise QuoteFeedForbidden(
            f"quote_feed refuses to start: LIVE_TRADING={live!r} (must be 'false')."
        )


def _append(record: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True, help="symbol to quote, e.g. XAUUSD")
    ap.add_argument("--provider", default="mock", choices=sorted(PROVIDERS.keys()))
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between fetches")
    ap.add_argument("--output", default="logs/quotes.jsonl")
    ap.add_argument("--max-iterations", type=int, default=0,
                    help="stop after N quotes; 0 = run forever until SIGINT")
    ap.add_argument("--once", action="store_true", help="shorthand for --max-iterations 1")
    ap.add_argument("--seed", type=int, default=0, help="seed for deterministic mock provider")
    ap.add_argument("--base-price", type=float, default=2400.0, help="base price for mock provider")
    args = ap.parse_args()

    _refuse_if_live()

    provider_cls = PROVIDERS[args.provider]
    if args.provider == "mock":
        provider = provider_cls(seed=args.seed, base_price=args.base_price)
    else:
        provider = provider_cls()

    out_path = Path(args.output)
    max_iter = 1 if args.once else max(0, args.max_iterations)
    interval = max(0.0, float(args.interval))

    print(
        f"[quote_feed] provider={args.provider} symbol={args.symbol} "
        f"interval={interval}s max_iter={max_iter or 'infinite'} "
        f"output={out_path}"
    )

    iteration = 0
    try:
        while True:
            quote = provider.fetch(args.symbol)
            record = quote.to_dict()
            _append(record, out_path)
            iteration += 1
            print(
                f"[quote_feed] #{iteration} {record['symbol']} "
                f"bid={record['bid']:.2f} ask={record['ask']:.2f} last={record['last']:.2f} "
                f"source={record['source']}"
            )
            if max_iter and iteration >= max_iter:
                break
            if interval > 0:
                time.sleep(interval)
    except KeyboardInterrupt:
        print(f"[quote_feed] interrupted after {iteration} quotes")
    finally:
        provider.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
