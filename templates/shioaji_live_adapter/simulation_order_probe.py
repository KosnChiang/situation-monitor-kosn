"""Sinotrade Shioaji simulation order smoke probe -- OUT-OF-TREE template (Phase 7.C).

This file is part of the templates/shioaji_live_adapter/ TEMPLATE. It MUST be
copied OUT of the main repo before running. The main repo never imports shioaji.

What it does
------------
Exercises the Shioaji order path end-to-end against the SIMULATION account.

* By default the probe is DRY-RUN: it logs the intended order to JSONL and
  exits WITHOUT calling ``api.place_order``.
* Pass ``--confirm-simulation-submit`` to actually call ``api.place_order``
  (still against the simulation account -- ``sj.Shioaji(simulation=True)`` is
  hard-pinned at construction).
* Optional ``--cancel-after N``: after submit, sleep N seconds then call
  ``api.cancel_order(trade)`` and log the result.

Safety envelope
---------------
* lazy ``import shioaji`` inside ``main()`` -- file loads without the SDK
* refuses to start unless ``SHIOAJI_SIMULATION`` is unset or truthy
  (``1`` / ``true`` / ``yes`` / ``on`` -- default unset behaves as truthy)
* ``sj.Shioaji(simulation=True)`` is hard-pinned at construction
* ``api.place_order`` is gated by ``--confirm-simulation-submit`` so a typo
  in the command line cannot fire an order
* never imports from the main repo

Run dry-run (safe, never submits)::

    cd C:\\Trading\\live-adapters\\shioaji
    .\\.venv\\Scripts\\Activate.ps1
    $env:SHIOAJI_SIMULATION = "true"
    python simulation_order_probe.py --code TMFR1 --side LONG --qty 1

Run real simulation submit (still simulation account, but does call
``place_order``)::

    python simulation_order_probe.py --code TMFR1 --side LONG --qty 1 `
        --confirm-simulation-submit

Run simulation submit + cancel after 5s::

    python simulation_order_probe.py --code TMFR1 --side LONG --qty 1 `
        --confirm-simulation-submit --cancel-after 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


# ---------- credentials (mirrors live_adapter.py to keep this script self-contained) ----

def _load_credentials() -> tuple[str, str, str]:
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
    raw = (os.getenv("SHIOAJI_SIMULATION", "true") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _append_json_line(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _resolve_tmf_contract(api, requested_code: str | None):
    """Pick the TMF (micro-TX) contract to submit against.

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
        description="Shioaji simulation order smoke probe "
                    "(template-side, dry-run by default).",
    )
    ap.add_argument("--code", default="",
                    help="TMF contract code, e.g. TMFR1. Empty = auto-pick nearest TMF.")
    ap.add_argument("--side", choices=("LONG", "SHORT"), required=True)
    ap.add_argument("--qty", type=int, default=1,
                    help="contracts to submit (default 1).")
    ap.add_argument("--price-type", choices=("MKT", "LMT"), default="MKT")
    ap.add_argument("--limit-price", type=float, default=0.0,
                    help="required when --price-type LMT.")
    ap.add_argument("--order-type", choices=("IOC", "ROD", "FOK"), default="IOC")
    ap.add_argument("--output", default="logs/shioaji_simulation_smoke.jsonl",
                    help="audit JSONL path.")
    ap.add_argument("--confirm-simulation-submit", action="store_true",
                    help="REQUIRED to actually call api.place_order. Without "
                         "this flag the probe runs dry-run only (logs intent, "
                         "does not submit).")
    ap.add_argument("--cancel-after", type=int, default=0,
                    help="if > 0, sleep N seconds after submit, then call "
                         "api.cancel_order(trade) and log the result.")
    args = ap.parse_args(argv)

    # ---- hard guard 1: SHIOAJI_SIMULATION must be truthy ----
    if not _is_simulation_env():
        print(
            "[simulation_order_probe] REFUSE: SHIOAJI_SIMULATION must be "
            "'true' to run this probe. Set $env:SHIOAJI_SIMULATION='true' "
            "and retry.",
            file=sys.stderr,
        )
        return 2

    # ---- hard guard 2: qty sanity ----
    if args.qty <= 0:
        print("[simulation_order_probe] REFUSE: --qty must be > 0.",
              file=sys.stderr)
        return 2

    if args.price_type == "LMT" and args.limit_price <= 0:
        print(
            "[simulation_order_probe] REFUSE: --price-type LMT requires "
            "--limit-price > 0.",
            file=sys.stderr,
        )
        return 2

    out_path = Path(args.output)
    intent_ts = time.time()
    intent_iso = datetime.now(timezone.utc).isoformat()

    # Lazy shioaji import -- module top must remain SDK-free.
    import shioaji as sj

    api_key, secret_key, _ = _load_credentials()
    api = sj.Shioaji(simulation=True)
    api.login(api_key=api_key, secret_key=secret_key)
    print("[simulation_order_probe] login ok, simulation=True")

    try:
        contract = _resolve_tmf_contract(api, args.code or None)
        code = getattr(contract, "code", args.code or "TMF")
        print(f"[simulation_order_probe] resolved contract code={code}")

        intent = {
            "kind": "intent",
            "ts": intent_ts,
            "timestamp": intent_iso,
            "code": code,
            "side": args.side,
            "qty": args.qty,
            "price_type": args.price_type,
            "limit_price": args.limit_price,
            "order_type": args.order_type,
            "dry_run": not args.confirm_simulation_submit,
        }
        _append_json_line(out_path, intent)
        print(f"[simulation_order_probe] intent logged: {intent}")

        # ---- DRY-RUN: stop before any place_order call ----
        if not args.confirm_simulation_submit:
            print(
                "[simulation_order_probe] DRY-RUN: --confirm-simulation-submit "
                "not set; skipping api.place_order. Intent was logged to "
                f"{out_path}.",
            )
            return 0

        # ---- Live (simulation account) submit ----
        action = (sj.constant.Action.Buy if args.side == "LONG"
                  else sj.constant.Action.Sell)
        price_type = (sj.constant.FuturesPriceType.MKT
                      if args.price_type == "MKT"
                      else sj.constant.FuturesPriceType.LMT)
        order_type_map = {
            "IOC": sj.constant.OrderType.IOC,
            "ROD": sj.constant.OrderType.ROD,
            "FOK": sj.constant.OrderType.FOK,
        }
        order = api.Order(
            action=action,
            price=float(args.limit_price if args.price_type == "LMT" else 0.0),
            quantity=int(args.qty),
            price_type=price_type,
            order_type=order_type_map[args.order_type],
            octype=sj.constant.FuturesOCType.Auto,
            account=api.futopt_account,
        )
        trade = api.place_order(contract, order)
        submitted_ts = time.time()
        submitted_iso = datetime.now(timezone.utc).isoformat()

        try:
            api.update_status(api.futopt_account)
        except Exception:
            pass

        status = ""
        order_id = ""
        fill_price = 0.0
        try:
            st = trade.status.status
            status = str(getattr(st, "value", st))
        except Exception:
            pass
        try:
            order_id = str(getattr(trade.order, "id", "") or "")
        except Exception:
            pass
        try:
            fill_price = float(getattr(trade.order, "price", 0.0) or 0.0)
        except Exception:
            pass

        submit_record = {
            "kind": "submit",
            "ts": submitted_ts,
            "timestamp": submitted_iso,
            "code": code,
            "side": args.side,
            "qty": args.qty,
            "price_type": args.price_type,
            "order_type": args.order_type,
            "status": status,
            "order_id": order_id,
            "fill_price": fill_price,
        }
        _append_json_line(out_path, submit_record)
        print(f"[simulation_order_probe] submitted: {submit_record}")

        if args.cancel_after > 0:
            print(
                f"[simulation_order_probe] sleeping {args.cancel_after}s "
                "before cancel...",
            )
            time.sleep(args.cancel_after)
            cancel_ok = False
            cancel_err = ""
            try:
                api.cancel_order(trade)
                cancel_ok = True
            except Exception as exc:
                cancel_err = str(exc)
                print(
                    f"[simulation_order_probe] cancel raised: {exc}",
                    file=sys.stderr,
                )
            cancel_record = {
                "kind": "cancel",
                "ts": time.time(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "code": code,
                "order_id": order_id,
                "cancel_ok": cancel_ok,
                "cancel_error": cancel_err,
            }
            _append_json_line(out_path, cancel_record)
            print(f"[simulation_order_probe] cancel recorded: {cancel_record}")
    finally:
        try:
            api.logout()
        except Exception:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
