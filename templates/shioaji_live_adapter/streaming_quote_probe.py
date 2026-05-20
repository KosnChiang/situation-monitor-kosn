"""Sinotrade Shioaji streaming quote probe -- OUT-OF-TREE template (Phase 7.B-1).

This file is part of the templates/shioaji_live_adapter/ TEMPLATE. It MUST be
copied OUT of the main repo before running. The main repo never imports shioaji.

What it does
------------
Subscribes to streaming TICK and/or BIDASK frames for a single TMF (micro-TX)
contract via api.quote.subscribe(...), writes each received frame as one JSON
line to --output, then unsubscribes and logs out after --seconds.

JSONL row shape matches the main repo's Quote schema so
``tools/mock_fibo_signal.py --price-source latest_quote`` can consume it
unmodified::

    {"symbol":"TMFR1","bid":..,"ask":..,"last":..,
     "ts":..,"timestamp":"..Z","source":"shioaji-tick"}

Safety envelope
---------------
* lazy ``import shioaji`` inside ``main()`` -- file loads without the SDK
* refuses to start unless ``SHIOAJI_SIMULATION`` is unset or truthy
  (``1`` / ``true`` / ``yes`` / ``on`` -- default unset behaves as truthy)
* ``sj.Shioaji(simulation=True)`` is hard-pinned at construction
* finite ``--seconds`` flag; the probe never runs forever
* NEVER calls ``api.place_order`` / ``submit_order`` -- read-only by design
* never imports from the main repo (no ``from app``, ``from ai_swing``,
  ``from live``, ``from quote``, ``from tools``, ``from strategy``,
  ``from risk``, ``from executor``, ``from notify``, ``from vision``,
  ``from capture``)

Run::

    cd C:\\Trading\\live-adapters\\shioaji
    .\\.venv\\Scripts\\Activate.ps1
    $env:SHIOAJI_SIMULATION = "true"
    python streaming_quote_probe.py --code TMFR1 --seconds 30 `
        --output logs\\probe_quotes.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------- credentials (mirrors live_adapter.py so this script is self-contained) ----

def _load_credentials() -> tuple[str, str, str]:
    """Return ``(api_key, secret_key, ca_password)``.

    Resolution order:
      1. Windows Credential Manager via ``keyring`` (service=trading_shioaji)
      2. Env fallback: ``SHIOAJI_API_KEY`` / ``SHIOAJI_SECRET_KEY`` /
         ``SHIOAJI_CA_PASSWORD``
    Raises ``RuntimeError`` when either api_key or secret_key is empty after
    both sources have been tried.
    """
    api_key = ""
    secret_key = ""
    ca_password = ""
    try:
        import keyring
        api_key = keyring.get_password("trading_shioaji", "api_key") or ""
        secret_key = keyring.get_password("trading_shioaji", "secret_key") or ""
        ca_password = keyring.get_password("trading_shioaji", "ca_password") or ""
    except Exception:
        pass
    if not api_key:
        api_key = (os.getenv("SHIOAJI_API_KEY", "") or "").strip()
    if not secret_key:
        secret_key = (os.getenv("SHIOAJI_SECRET_KEY", "") or "").strip()
    if not ca_password:
        ca_password = (os.getenv("SHIOAJI_CA_PASSWORD", "") or "").strip()
    if not api_key or not secret_key:
        raise RuntimeError(
            "Shioaji credentials missing. Set via keyring "
            "(service='trading_shioaji', keys='api_key'/'secret_key') "
            "or env vars SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY."
        )
    return api_key, secret_key, ca_password


def _is_simulation_env() -> bool:
    """Truthy when SHIOAJI_SIMULATION is unset (default) or in
    ('1','true','yes','on'). Any other value (including 'false') returns False.
    """
    raw = (os.getenv("SHIOAJI_SIMULATION", "true") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


# ---------- helpers ----------

def _append_json_line(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _resolve_tmf_contract(api, requested_code: str | None):
    """Pick the TMF (micro-TX) contract to subscribe against.

    Mirrors the Phase 7.A-2 resolver in ``live_adapter.py``: prefer
    attribute access on the ``api.Contracts.Futures.TMF`` sub-namespace
    (shioaji 1.3.x shape, where ``for c in api.Contracts.Futures``
    yields nothing on a paper session) before falling back to legacy
    iteration. Adds step 0 for the ``--code`` CLI flag so the operator
    can pin a specific contract.

    Resolution order:

      0. If ``requested_code`` is truthy:
         a. ``api.Contracts.Futures.TMF.<requested_code>`` attribute
         b. Iterate ``api.Contracts.Futures`` and match ``c.code``
         Raise if neither finds it -- operator asked for a specific
         contract, do not silently pick a different one.
      1. ``api.Contracts.Futures.TMF.TMFR1`` -- broker-maintained
         front-month roll alias. Default pick.
      2. Nearest dated ``TMF<YYYYMM>`` under
         ``api.Contracts.Futures.TMF`` by lexical sort of YYYYMM.
      3. Legacy fallback: iterate ``api.Contracts.Futures`` and pick by
         code/name match, sorted by ``delivery_date`` /
         ``delivery_month`` / ``underlying_kind``.

    Raises ``RuntimeError`` with an explicit "checked X, Y, Z" message
    when nothing matches.
    """
    futures = api.Contracts.Futures
    tmf_ns = getattr(futures, "TMF", None)

    # ---- 0. operator-specified code ----
    if requested_code:
        if tmf_ns is not None:
            found = getattr(tmf_ns, requested_code, None)
            if found is not None:
                return found
        try:
            for c in futures:
                if getattr(c, "code", "") == requested_code:
                    return c
        except TypeError:
            pass
        raise RuntimeError(
            f"requested TMF contract code {requested_code!r} not found "
            f"(checked api.Contracts.Futures.TMF.{requested_code} and "
            "Contracts.Futures iteration)"
        )

    # ---- 1 + 2. attribute path (shioaji 1.3.x shape) ----
    if tmf_ns is not None:
        front = getattr(tmf_ns, "TMFR1", None)
        if front is not None:
            return front
        dated: list[tuple[str, object]] = []
        for attr in dir(tmf_ns):
            if not attr.startswith("TMF"):
                continue
            suffix = attr[3:]
            if len(suffix) == 6 and suffix.isdigit():
                c = getattr(tmf_ns, attr, None)
                if c is not None:
                    dated.append((suffix, c))
        if dated:
            dated.sort(key=lambda x: x[0])
            return dated[0][1]

    # ---- 3. legacy iteration fallback ----
    candidates = []
    try:
        for c in futures:
            code = getattr(c, "code", "") or ""
            name = getattr(c, "name", "") or ""
            if (code.startswith("TMF")
                    or "微型台指" in name
                    or "Mini TX" in name.replace(" ", "")):
                candidates.append(c)
    except TypeError:
        candidates = []
    except Exception as exc:
        raise RuntimeError(f"failed to enumerate futures: {exc}") from exc

    if not candidates:
        raise RuntimeError(
            "no micro-TX (TMF / 微型台指) contract found in Shioaji catalog "
            "(checked api.Contracts.Futures.TMF.TMFR1, dated "
            "TMFYYYYMM, and Contracts.Futures iteration)"
        )
    candidates.sort(key=lambda c: (
        getattr(c, "delivery_date", "")
        or getattr(c, "delivery_month", "")
        or getattr(c, "underlying_kind", "")
        or ""
    ))
    return candidates[0]


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Shioaji streaming quote probe (template-side, simulation only)."
    )
    ap.add_argument("--code", default="",
                    help="TMF contract code (e.g. TMFR1). Empty = auto-pick nearest TMF.")
    ap.add_argument("--seconds", type=int, default=30,
                    help="probe duration in seconds (must be > 0). Default 30.")
    ap.add_argument("--output", default="logs/probe_quotes.jsonl",
                    help="JSONL path to append every received frame.")
    ap.add_argument("--quote-type", choices=("tick", "bidask", "both"),
                    default="bidask",
                    help="which streaming frame(s) to subscribe to.")
    args = ap.parse_args(argv)

    # ---- hard guard 1: SHIOAJI_SIMULATION must be truthy ----
    if not _is_simulation_env():
        print(
            "[streaming_quote_probe] REFUSE: SHIOAJI_SIMULATION must be "
            "'true' to run this probe. Set $env:SHIOAJI_SIMULATION='true' "
            "and retry.",
            file=sys.stderr,
        )
        return 2

    # ---- hard guard 2: finite duration ----
    if args.seconds <= 0:
        print("[streaming_quote_probe] REFUSE: --seconds must be > 0.",
              file=sys.stderr)
        return 2

    out_path = Path(args.output)

    # Lazy shioaji import -- module top must remain SDK-free.
    import shioaji as sj

    api_key, secret_key, _ = _load_credentials()
    api = sj.Shioaji(simulation=True)
    api.login(api_key=api_key, secret_key=secret_key)
    print("[streaming_quote_probe] login ok, simulation=True")

    contract = None
    try:
        contract = _resolve_tmf_contract(api, args.code or None)
        code = getattr(contract, "code", args.code or "TMF")
        print(f"[streaming_quote_probe] resolved contract code={code}")

        frames_seen = {"count": 0}
        lock = threading.Lock()

        def _emit(kind: str, payload: dict) -> None:
            now = time.time()
            iso = datetime.now(timezone.utc).isoformat()
            row = {
                "symbol": code,
                "bid": float(payload.get("bid", 0.0) or 0.0),
                "ask": float(payload.get("ask", 0.0) or 0.0),
                "last": float(payload.get("last", 0.0) or 0.0),
                "ts": now,
                "timestamp": iso,
                "source": f"shioaji-{kind}",
            }
            with lock:
                _append_json_line(out_path, row)
                frames_seen["count"] += 1

        # Defensive attribute reads -- Shioaji frame field names vary across
        # SDK versions; missing fields become 0.0, which is fine for a probe.
        def on_tick(_exchange, tick) -> None:
            payload = {
                "last": getattr(tick, "close", None) or getattr(tick, "price", 0.0),
                "bid":  getattr(tick, "bid_price", 0.0) or 0.0,
                "ask":  getattr(tick, "ask_price", 0.0) or 0.0,
            }
            _emit("tick", payload)

        def on_bidask(_exchange, bidask) -> None:
            bid_list = getattr(bidask, "bid_price", []) or []
            ask_list = getattr(bidask, "ask_price", []) or []
            payload = {
                "bid":  float(bid_list[0]) if bid_list else 0.0,
                "ask":  float(ask_list[0]) if ask_list else 0.0,
                "last": 0.0,
            }
            _emit("bidask", payload)

        if args.quote_type in ("tick", "both"):
            api.quote.set_on_tick_fop_v1_callback(on_tick)
            api.quote.subscribe(
                contract,
                quote_type=sj.constant.QuoteType.Tick,
                version=sj.constant.QuoteVersion.v1,
            )
            print("[streaming_quote_probe] subscribed tick")

        if args.quote_type in ("bidask", "both"):
            api.quote.set_on_bidask_fop_v1_callback(on_bidask)
            api.quote.subscribe(
                contract,
                quote_type=sj.constant.QuoteType.BidAsk,
                version=sj.constant.QuoteVersion.v1,
            )
            print("[streaming_quote_probe] subscribed bidask")

        # Shioaji fires callbacks on its own threads; we just sleep here.
        time.sleep(args.seconds)
        print(f"[streaming_quote_probe] frames_received={frames_seen['count']}")
    finally:
        if contract is not None:
            for qt_attr in ("Tick", "BidAsk"):
                try:
                    api.quote.unsubscribe(
                        contract,
                        quote_type=getattr(sj.constant.QuoteType, qt_attr),
                    )
                except Exception:
                    pass
        try:
            api.logout()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
