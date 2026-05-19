"""watch_ai_swing_webhook.py -- 24h AI swing watcher (Phase 7.A).

Starts the FastAPI server (app.main:app) plus a background thread
that writes a heartbeat to ``logs/ai_swing_watch_heartbeat.jsonl``
every N seconds (default 60). Operator runs this once at session
start.

The webhook itself is the existing ``/webhook/ai-swing`` route in
``app/main.py``. This launcher does not implement any new HTTP path;
it only spins the uvicorn server plus the heartbeat.

Mock-only: no broker SDK, no outbound HTTP, no broker credential read.
The heartbeat writes status + ts only.
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_swing.logging import write_heartbeat


def _heartbeat_loop(interval_seconds: float, stop_event: threading.Event) -> None:
    iteration = 0
    while not stop_event.is_set():
        iteration += 1
        write_heartbeat({
            "status": "alive",
            "iteration": iteration,
            "ts": time.time(),
            "pid": os.getpid(),
            "interval_seconds": interval_seconds,
        })
        # Sleep in small chunks so stop_event interrupts promptly.
        sleep_left = interval_seconds
        while sleep_left > 0 and not stop_event.is_set():
            chunk = min(1.0, sleep_left)
            time.sleep(chunk)
            sleep_left -= chunk


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Start the 24h AI swing webhook watcher.",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--heartbeat-seconds", type=float, default=60.0,
                    help="Seconds between heartbeat writes (default 60).")
    ap.add_argument(
        "--once",
        action="store_true",
        help=(
            "Write one heartbeat and exit immediately. Useful for tests; "
            "operator runs without --once in production."
        ),
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.once:
        write_heartbeat({
            "status": "alive",
            "iteration": 1,
            "ts": time.time(),
            "pid": os.getpid(),
            "interval_seconds": args.heartbeat_seconds,
            "once": True,
        })
        return 0

    stop_event = threading.Event()
    hb_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(args.heartbeat_seconds, stop_event),
        daemon=True,
    )
    hb_thread.start()

    try:
        import uvicorn
    except ImportError:
        print("ERROR: uvicorn is required to run the watcher.", file=sys.stderr)
        stop_event.set()
        return 2

    try:
        uvicorn.run("app.main:app", host=args.host, port=args.port)
    finally:
        stop_event.set()
        hb_thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
