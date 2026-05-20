# Shioaji Live Adapter

Operator-facing notes for the Sinotrade Shioaji adapter template that
ships under `templates/shioaji_live_adapter/`.

> **Phase 7.A-1 update**: the template has been aligned with the
> Shioaji 1.2.x official API. Futures `order_type` now uses
> `sj.constant.OrderType` (the shared enum), not the non-existent
> `FuturesOrderType`. `requirements.txt` pins `shioaji>=1.2.0,<2.0.0`
> to track the current stable line and stay clear of a future 2.x
> breaking-change boundary.
>
> **Phase 7.A-2 update**: `_resolve_tmf_contract` now prefers
> attribute access on `api.Contracts.Futures.TMF` over iteration of
> `api.Contracts.Futures`. Operator verification on shioaji 1.3.3
> (paper session) showed iteration returns no items, while the
> `TMF` sub-namespace is fully populated (`TMFR1`, `TMFR2`,
> `TMF202605` ... `TMF202703`). Iteration is still tried as a final
> fallback for older SDK shapes.

The template is meant to be **copied out** of the main repo into the
operator's own working directory (default
`C:\Trading\live-adapters\shioaji\`). The main repo never imports
Shioaji and never lists it as a dependency.

## Why the template lives in main repo

Convenience. Operators can `git clone` this repo and have a starting
point. The template is **not** wired into the main repo's runtime; the
loader (`live.adapter_loader`) refuses to load anything from inside
the repo (`rejected_in_tree`), so a stray `import shioaji` inside the
template cannot accidentally execute in the main process.

The template's correctness as a main-repo file is verified by
`tests/test_shioaji_adapter_template.py`:

- File exists at the expected path.
- `class LiveAdapter` is defined.
- `import shioaji` appears (proves it's the right template).
- Top-level imports do not happen at module load (lazy imports inside
  methods only) so the file can be inspected by linters without the
  SDK installed.

## Where to copy it

```powershell
New-Item -ItemType Directory -Path C:\Trading\live-adapters\shioaji -Force
Copy-Item C:\trading\ai_fibo_vision_trader\templates\shioaji_live_adapter\* `
    C:\Trading\live-adapters\shioaji\ -Recurse
```

After the copy, the main repo's `live.adapter_loader` will accept
`LIVE_BROKER_ADAPTER_PATH=C:\Trading\live-adapters\shioaji\live_adapter.py`
provided that path is also covered by `LIVE_ADAPTER_ALLOWLIST`.

## Credentials

See `templates/shioaji_live_adapter/README.md` for the keyring +
env-fallback details. The TL;DR:

```powershell
cd C:\Trading\live-adapters\shioaji
.\.venv\Scripts\Activate.ps1
python -m keyring set "trading_shioaji" "api_key"
python -m keyring set "trading_shioaji" "secret_key"
```

The main repo's structural guard (`test_main_repo_does_not_import_keyring`
in `tests/test_phase6b5_docs_exist.py`) excludes the `templates/`
directory from its scan, so the template's `keyring` usage does not
contaminate the main repo.

## Simulation toggle

`SHIOAJI_SIMULATION=true` -> paper / simulator. Default.
`SHIOAJI_SIMULATION=false` -> real money. Requires deliberate opt-in.

The Phase 6.B-5 `docs/micro_live_readiness_checklist.md` MUST be
ticked before flipping to `false`.

## TMF (micro-TX) contract resolution

Phase 7.A-2 resolution order:

1. `api.Contracts.Futures.TMF.TMFR1` -- broker-maintained
   front-month roll alias. Preferred.
2. Nearest dated `TMFYYYYMM` under `api.Contracts.Futures.TMF`,
   sorted lexically by `YYYYMM`.
3. Legacy iteration over `api.Contracts.Futures` matching
   `TMF*` / `微型台指` / `Mini TX`. Fallback only.

The operator may need to refine for roll-over behaviour (e.g.
prefer `TMFR2` near expiry).

Point value: **NT$10 / point / contract** (hardcoded in
`ai_swing.tmf_pnl.POINT_VALUE_TWD`).

## End-to-end smoke

Before placing any real-money order:

1. Copy template to out-of-tree path.
2. Install template's venv + `requirements.txt`.
3. Set keyring credentials (paper account first).
4. Set `SHIOAJI_SIMULATION=true`.
5. Run `tools.run_live_order --adapter-source=external --ai-decision <test.json>`.
6. Verify `logs/live_orders.jsonl` + `logs/live_fills.jsonl` populate.
7. Verify Shioaji's paper account UI shows the order.
8. Repeat for at least 50 orders covering ENTRY / cancel / partial /
   timeout (see `docs/live_adapter_runbook.md` section 5).
9. ONLY THEN consider `SHIOAJI_SIMULATION=false` for the first real
   trade, gated by `docs/micro_live_readiness_checklist.md`.

## What the main repo enforces

- The loader refuses any adapter path inside the main repo
  (`rejected_in_tree`).
- The loader refuses any adapter path outside the allowlist
  (`rejected_not_in_allowlist`).
- The loader refuses any plugin module whose `LiveAdapter` does not
  satisfy `LiveBrokerAdapterProtocol`.
- The CLI's `--adapter-source` defaults to `fake`. External path is
  opt-in per invocation.
- The KillSwitch pre-check fires before any adapter load.
- `MicroLiveGate` runs the TMF risk math (`NT$10 / point`) at the
  pipeline layer (not the adapter), so the adapter cannot circumvent
  the per-trade or per-day caps.

## What the main repo does NOT enforce

- That the operator's plugin file is what they think it is. The
  `path_sha256` field in `logs/live_adapter_loads.jsonl` lets the
  operator cross-check, but the main repo does not run integrity
  checks beyond that hash.
- That broker credentials in the operator's keychain are valid; the
  plugin will raise at `connect()` if not.
- The actual Shioaji session lifecycle (login, retry, timeout, roll);
  that's plugin territory.
