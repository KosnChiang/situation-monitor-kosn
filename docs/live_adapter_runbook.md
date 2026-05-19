# Live Adapter Runbook

This runbook is for operators who want to wire a real broker into the
Phase 6.B live execution path. The main repo ships **no real broker
integration**; this document specifies the contract that a real
out-of-tree adapter must satisfy, where it must live, and how its
credentials must be stored.

If you are reading this, you have already verified:

- `scripts\test.ps1` is fully green on the current commit.
- `Phase 6.B-4` (`5745a7b` or later) is checked out.
- You understand that any real trade placed via this path is **your
  decision**, not the bot's.

---

## 1. Where the adapter lives

The adapter is **out-of-tree**. It must NOT live anywhere under the
main repo directory. The loader's `_validate_path` gate enforces this:
any path that resolves inside the repo root is rejected with
`rejected_in_tree`, and the audit log records the rejection.

**Recommended location:**

```
C:\Trading\live-adapters\<broker>\
    live_adapter.py         # the only file the main repo loads
    credentials_helper.py   # wraps keyring; main repo never reads it
    requirements.txt        # broker SDK + keyring; NOT main repo's reqs
    tests\
        test_adapter_against_paper.py
    README.md
    CHANGELOG.md
```

The directory `C:\Trading\live-adapters\` is the default allowlist
root recognised by `live.adapter_loader._get_allowlist`. Override via
env `LIVE_ADAPTER_ALLOWLIST` (comma-separated paths).

**Self-protection:** the loader silently drops any allowlist entry
that resolves inside the repo root. There is no env trick that lets
a plugin live in the main repo.

---

## 2. The adapter contract

The plugin module must expose a class named **exactly** `LiveAdapter`.
That class must satisfy `live.broker_adapter_protocol.LiveBrokerAdapterProtocol`:

| Member | Signature | Purpose |
|---|---|---|
| `name` | `str` (class attr) | Audit identifier |
| `version` | `str` (class attr, optional) | Audit version stamp |
| `connect()` | `-> None` | Open broker session |
| `submit_order(order)` | `(LiveOrder) -> LiveFill` | Send one order |
| `cancel_order(order_id)` | `(str) -> bool` | Cancel by id |
| `positions()` | `-> list[dict]` | Open positions snapshot |
| `account_equity()` | `-> float` | Cash + unrealised |
| `disconnect()` | `-> None` | Tear down session |

**Skeleton (operator copies, fills in, places in `<allowlist>/<broker>/live_adapter.py`):**

```python
"""Operator-owned LiveAdapter for <broker>.

Lives OUT-OF-TREE. The main repo's loader imports this module via
LIVE_BROKER_ADAPTER_PATH; the main repo never imports broker SDK,
never reads broker credentials, never references this file's
implementation directly.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


class LiveAdapter:
    name = "my-broker-v1"
    version = "1.0.0"

    def __init__(self):
        # Credentials come from the OS keychain only. See section 4.
        # The constructor is a good place to fail loudly if a credential
        # is missing -- earlier failure = clearer operator signal.
        from credentials_helper import load_my_broker_credentials
        self._creds = load_my_broker_credentials()
        self._session = None

    def connect(self):
        # Open broker session. Wrap broker SDK here.
        # Example (do NOT copy into main repo):
        #
        #     import some_broker_sdk
        #     self._session = some_broker_sdk.Session(
        #         api_key=self._creds["api_key"],
        #         api_secret=self._creds["api_secret"],
        #     )
        #     self._session.login()
        raise NotImplementedError("Operator must implement.")

    def submit_order(self, order):
        # 1. Translate live.models.LiveOrder -> broker's order schema.
        # 2. Call broker SDK.
        # 3. Translate broker response -> live.models.LiveFill.
        # 4. Write to the main repo's audit log helpers (section 3).
        from live.audit_log import write_live_order, write_live_fill
        from live.models import LiveFill

        write_live_order(order)

        # ... broker call ...
        # broker_response = self._session.place_order(...)

        ts = time.time()
        iso = datetime.now(timezone.utc).isoformat()
        fill = LiveFill(
            order_id=order.order_id,
            ts=ts,
            timestamp=iso,
            side=order.side,
            fill_price=broker_response.average_fill_price,   # noqa
            qty=broker_response.filled_qty,                  # noqa
            slippage=abs(broker_response.average_fill_price - order.entry),
            status=broker_response.status,
        )
        write_live_fill(fill)
        return fill

    def cancel_order(self, order_id):
        # Send cancel to the broker. Return True if accepted.
        raise NotImplementedError

    def positions(self):
        # Return a list of position dicts the operator's tooling can
        # consume. Shape is operator-defined in v1.
        return []

    def account_equity(self):
        # Cash balance + open position unrealised P&L.
        return 0.0

    def disconnect(self):
        # Close broker session cleanly.
        pass
```

---

## 3. Audit log contract

The Phase 6.B pipeline does NOT write `logs/live_orders.jsonl` or
`logs/live_fills.jsonl` itself. The adapter must populate them using
the main repo's helpers:

```python
from live.audit_log import write_live_order, write_live_fill

write_live_order(order)   # before broker call (so we have a row even on failure)
write_live_fill(fill)     # after successful fill
```

These helpers respect env-driven log paths:

- `LIVE_ORDERS_LOG` (default `logs/live_orders.jsonl`)
- `LIVE_FILLS_LOG` (default `logs/live_fills.jsonl`)
- `LIVE_REJECTIONS_LOG` (default `logs/live_rejections.jsonl`)

The adapter must NOT:

- Write to `logs/trades.jsonl` (MockExecutor's territory).
- Write to `logs/paper_trades.jsonl` (PaperExecutor's territory).
- Write log lines anywhere outside the configured `logs/` paths.
- Include any credential, session token, account number, or other
  identifying secret in any log line.

If the broker raises, let the exception propagate. The pipeline
catches it and writes a `LiveRejection` with `rejection_layer="adapter"`.
Suppressing exceptions silently breaks the audit chain.

---

## 4. Credentials

Broker credentials MUST be stored in the OS-level credential store.
On Windows, this is **Windows Credential Manager**, accessed from
Python via the `keyring` package.

### One-time setup

In a PowerShell session, run inside the **adapter's own venv** (NOT
the main repo's `.venv`):

```powershell
cd C:\Trading\live-adapters\<broker>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install keyring <broker-sdk>

# Store each credential. The prompt does not echo to history.
python -m keyring set "trading_<broker>" "api_key"
python -m keyring set "trading_<broker>" "api_secret"
# ... repeat for every credential the broker needs ...
```

### Reading at runtime

In the adapter's `__init__` only (so missing credentials fail early):

```python
import keyring

api_key = keyring.get_password("trading_<broker>", "api_key")
if api_key is None:
    raise RuntimeError(
        "credential 'trading_<broker>/api_key' is not in the keychain; "
        "run `python -m keyring set ...` first"
    )
```

### What you must NOT do

- **`.env` files**: NEVER. The repo's `.env` is gitignored but
  unencrypted at rest; backups, screen shares, and stack traces all
  leak it.
- **CLI flags**: NEVER. They appear in shell history and Task Manager.
- **Process env vars**: NEVER. Child processes inherit them.
- **Hard-coded in source**: NEVER. `git log -p` and search engines find them.
- **Print to stdout/logs**: NEVER. Even for "debug only" --- forget once
  and the secret hits CI / Slack / pastebin.

### Main repo enforcement

The main repo's structural test asserts:

- No `.py` file under the main repo imports `keyring`.
- The main repo's `requirements.txt` does not include `keyring`.

So `keyring` lives only in the operator's own adapter venv. The main
repo never sees the package, never imports it, never reads a
credential under any name.

---

## 5. Smoke testing before micro-live

Before a real micro-live attempt, the adapter MUST be smoke-tested
against the broker's **paper / simulator** account. The main repo
cannot help with this; it's adapter-internal:

1. Write `tests/test_adapter_against_paper.py` inside the adapter's
   directory.
2. Use the broker's paper account credentials, stored separately in
   the keychain under a `_paper` suffix:
   `python -m keyring set "trading_<broker>_paper" "api_key"`.
3. Submit at least 50 simulated orders covering:
   - LONG fill
   - SHORT fill
   - Stop triggered
   - Target triggered
   - Partial fill (if the broker supports it)
   - Broker-side rejection (e.g. insufficient margin)
   - Broker-side cancel
   - Broker disconnect mid-submit
   - Broker timeout
4. Verify `submit_order` returns within 1 second on the happy path.
5. Verify `submit_order` raises (does NOT hang) on broker disconnect.
6. Verify `cancel_order` works when there is a real cancellable order.

Only after this passes should the operator proceed to
`docs/micro_live_readiness_checklist.md`.

---

## 6. Common mistakes

- **Importing main repo modules at adapter top-level**: avoid. Import
  inside methods so a partial-broken main repo doesn't prevent the
  adapter from being imported. Top-level imports for `live.audit_log`
  and `live.models` are acceptable but keep them minimal.
- **Long-running `submit_order`**: keep it under 1 second. The
  pipeline assumes synchronous, fast submissions. Long blocks interact
  poorly with the KillSwitch (which is checked before but not during
  a submit).
- **Swallowing broker exceptions**: don't. The pipeline catches
  `Exception` from `submit_order` and converts it to a `LiveRejection`
  row with `rejection_layer="adapter"`. If you swallow, the audit
  chain is broken.
- **Sharing state across pipeline runs**: don't. Each CLI invocation
  is a fresh Python process; the adapter starts cold every time.
  Operator-side state belongs in `account_equity()` queries to the
  broker or in operator-owned files.
- **Reading credentials in `connect()` instead of `__init__()`**:
  this delays the failure signal. Read in `__init__` so a missing
  credential surfaces before the pipeline starts.
- **Writing the broker's full response to logs**: filter first. Many
  brokers echo session tokens or account numbers in responses; never
  log raw responses.

---

## 7. What this runbook does NOT do

- Implement an adapter (operator's job).
- Recommend a specific broker (operator's choice).
- Store credentials (OS keychain's job).
- Mediate broker disputes (operator deals with broker directly).
- Authorise live trading (operator decides; main repo enforces gates).

The main repo's job is to enforce the safety envelope and provide a
clean Protocol. Everything beyond that is operator territory.
