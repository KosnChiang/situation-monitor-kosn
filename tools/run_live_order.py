"""run_live_order.py -- explicitly-named live order CLI (Phase 7.A).

Functionally identical to ``tools.dry_run_live_order`` but with a name
that does not lie. ``dry_run_*`` confuses operators heading to real
money; this CLI accepts the same flags and the same defaults
(fake-first, external opt-in), but the name says "live".

Pass-through wrapper. The behaviour is centralized in
``tools.dry_run_live_order`` so a fix in one place reaches both.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.dry_run_live_order import main as _delegate_main


def main(argv: list[str] | None = None) -> int:
    return _delegate_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
