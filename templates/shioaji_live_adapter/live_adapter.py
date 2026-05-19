"""Sinotrade Shioaji LiveAdapter -- OUT-OF-TREE template (Phase 7.A).

DO NOT use this file from inside the main repo. Copy this entire
directory to a path OUTSIDE the main repo, e.g.::

    C:\\Trading\\live-adapters\\shioaji\\live_adapter.py
    C:\\Trading\\live-adapters\\shioaji\\README.md
    C:\\Trading\\live-adapters\\shioaji\\requirements.txt

Then set:

    LIVE_BROKER_ADAPTER_PATH=C:\\Trading\\live-adapters\\shioaji\\live_adapter.py
    LIVE_ADAPTER_ALLOWLIST=C:\\Trading\\live-adapters

Credentials precedence (read in __init__):

    1. Windows Credential Manager via keyring:
         service = "trading_shioaji"
         keys    = "api_key", "secret_key", optional "ca_password"
    2. Env vars (fallback):
         SHIOAJI_API_KEY, SHIOAJI_SECRET_KEY, SHIOAJI_CA_PASSWORD

Simulation toggle (default: simulation=True for safety):

    SHIOAJI_SIMULATION=true   # paper / simulator (default)
    SHIOAJI_SIMULATION=false  # real money -- requires deliberate operator opt-in
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone


# ---------- credentials --------------------------------------------------


def _load_credentials() -> tuple[str, str, str]:
    """Returns (api_key, secret_key, ca_password). Empty string for missing
    optional fields. Raises if api_key or secret_key missing."""
    api_key = ""
    secret_key = ""
    ca_password = ""
    # Try keyring first (preferred).
    try:
        import keyring
        api_key = keyring.get_password("trading_shioaji", "api_key") or ""
        secret_key = keyring.get_password("trading_shioaji", "secret_key") or ""
        ca_password = keyring.get_password("trading_shioaji", "ca_password") or ""
    except Exception:
        pass
    # Fall back to env.
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


def _is_simulation() -> bool:
    val = (os.getenv("SHIOAJI_SIMULATION", "true") or "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ---------- LiveAdapter --------------------------------------------------


class LiveAdapter:
    """Implements the main repo's live.broker_adapter_protocol contract."""

    name = "shioaji-tmf-v1"
    version = "1.0.0"

    def __init__(self):
        self._api_key, self._secret_key, self._ca_password = _load_credentials()
        self._simulation = _is_simulation()
        self._api = None
        self._tmf_contract = None
        self._connected = False

    # ---- session lifecycle ----

    def connect(self) -> None:
        import shioaji as sj
        self._api = sj.Shioaji(simulation=self._simulation)
        self._api.login(api_key=self._api_key, secret_key=self._secret_key)
        self._tmf_contract = self._resolve_tmf_contract()
        self._connected = True

    def disconnect(self) -> None:
        if self._api is not None:
            try:
                self._api.logout()
            except Exception:
                pass
        self._connected = False

    # ---- TMF contract resolution ----

    def _resolve_tmf_contract(self):
        """Find the nearest tradable micro-TX contract dynamically.

        Operator NOTE: Shioaji's contract API returns objects whose
        date / month fields vary by SDK version. This function picks
        any contract whose code starts with 'TMF' or whose name
        contains the micro-TX label, then sorts by available date
        field. If no match, raises.
        """
        if self._api is None:
            raise RuntimeError("Shioaji api not initialised; call connect() first")
        candidates = []
        try:
            for c in self._api.Contracts.Futures:
                code = getattr(c, "code", "") or ""
                name = getattr(c, "name", "") or ""
                if (code.startswith("TMF")
                        or "微型台指" in name
                        or "Mini TX" in name.replace(" ", "")):
                    candidates.append(c)
        except Exception as exc:
            raise RuntimeError(f"failed to enumerate futures: {exc}") from exc
        if not candidates:
            raise RuntimeError(
                "no micro-TX (TMF / 微型台指) contract found in Shioaji catalog"
            )
        def _sort_key(c):
            return (
                getattr(c, "delivery_date", "")
                or getattr(c, "delivery_month", "")
                or getattr(c, "underlying_kind", "")
                or ""
            )
        candidates.sort(key=_sort_key)
        return candidates[0]

    # ---- order lifecycle ----

    def submit_order(self, order):
        if not self._connected:
            raise RuntimeError("Shioaji adapter not connected; call connect() first")
        import shioaji as sj
        from live.audit_log import write_live_fill, write_live_order
        from live.models import LiveFill

        write_live_order(order)

        action = (sj.constant.Action.Buy if order.side == "LONG"
                  else sj.constant.Action.Sell)
        sj_order = self._api.Order(
            action=action,
            price=float(order.entry),
            quantity=int(order.qty),
            price_type=sj.constant.FuturesPriceType.MKT,
            order_type=sj.constant.FuturesOrderType.IOC,
            octype=sj.constant.FuturesOCType.Auto,
            account=self._api.futopt_account,
        )

        trade = self._api.place_order(self._tmf_contract, sj_order)
        try:
            self._api.update_status(self._api.futopt_account)
        except Exception:
            pass

        fill_price = float(order.entry)
        status = "submitted"
        try:
            if hasattr(trade, "order") and hasattr(trade.order, "price"):
                fp = trade.order.price
                if fp:
                    fill_price = float(fp)
            if hasattr(trade, "status"):
                st = trade.status.status
                status = str(getattr(st, "value", st))
        except Exception:
            pass

        ts = time.time()
        iso = datetime.now(timezone.utc).isoformat()
        fill = LiveFill(
            order_id=order.order_id,
            ts=ts,
            timestamp=iso,
            side=order.side,
            fill_price=fill_price,
            qty=float(order.qty),
            slippage=abs(fill_price - float(order.entry)),
            status=status,
        )
        write_live_fill(fill)
        return fill

    def cancel_order(self, order_id: str) -> bool:
        # Operator NOTE: Shioaji's cancel API needs the Trade object,
        # not just a string id. Operator must maintain an order_id ->
        # Trade map (e.g., in self._trades). The skeleton returns False.
        return False

    def positions(self) -> list[dict]:
        if not self._connected:
            return []
        try:
            positions = self._api.list_positions(self._api.futopt_account)
        except Exception:
            return []
        out: list[dict] = []
        for p in positions:
            out.append({
                "code": getattr(p, "code", "") or "",
                "qty": int(getattr(p, "quantity", 0) or 0),
                "direction": str(getattr(p, "direction", "")),
                "price": float(getattr(p, "price", 0.0) or 0.0),
            })
        return out

    def account_equity(self) -> float:
        if not self._connected:
            return 0.0
        try:
            bal = self._api.account_balance()
        except Exception:
            return 0.0
        return float(getattr(bal, "balance", 0.0) or 0.0)
