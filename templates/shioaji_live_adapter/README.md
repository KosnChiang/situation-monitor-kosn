# Shioaji LiveAdapter Template

This directory is a **template** for an out-of-tree LiveAdapter that
wraps Sinotrade's Shioaji Python SDK. Copy it OUT of the main repo
before using; the main repo never imports or installs the Shioaji SDK.

## What this template gives you

| File | Purpose |
|---|---|
| `live_adapter.py` | The `class LiveAdapter` implementing the main repo's `LiveBrokerAdapterProtocol`. |
| `requirements.txt` | Python deps (`shioaji`, `keyring`). Install into the **adapter's own venv**, not the main repo's `.venv`. |
| `README.md` | This file. |

## Installation

```powershell
# 1. Create the out-of-tree directory.
New-Item -ItemType Directory -Path C:\Trading\live-adapters\shioaji -Force

# 2. Copy template files.
Copy-Item C:\trading\ai_fibo_vision_trader\templates\shioaji_live_adapter\* `
    C:\Trading\live-adapters\shioaji\ -Recurse

# 3. Create the adapter's own venv (kept SEPARATE from the main repo's .venv).
cd C:\Trading\live-adapters\shioaji
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Credentials setup

Use Windows Credential Manager via `keyring` (preferred):

```powershell
.\.venv\Scripts\Activate.ps1
python -m keyring set "trading_shioaji" "api_key"        # interactive prompt
python -m keyring set "trading_shioaji" "secret_key"
python -m keyring set "trading_shioaji" "ca_password"    # optional
```

Or as a fallback, set env vars (less secure, lands in process env):

```powershell
$env:SHIOAJI_API_KEY    = "..."
$env:SHIOAJI_SECRET_KEY = "..."
```

The adapter checks keyring first, then env. If both are empty,
construction raises `RuntimeError`.

## Simulation mode

```powershell
$env:SHIOAJI_SIMULATION = "true"   # paper / simulator (default)
$env:SHIOAJI_SIMULATION = "false"  # REAL MONEY -- only after deliberate opt-in
```

When the adapter constructs, it reads this env. If unset, defaults to
`true` (paper) for safety.

## Wiring into the main repo

```powershell
# In a fresh PowerShell, set ALL the LiveUnlockGate preconditions plus
# the adapter path:
$env:LIVE_TRADING       = "true"
$env:EXECUTION_MODE     = "live"
$env:LIVE_READY_FLAG    = "approved-" + (Get-Date -AsUTC -Format "yyyyMMdd")
$env:LIVE_TOKEN_HMAC    = -join ((48..57 + 97..102) | Get-Random -Count 32 | % {[char]$_})
$env:ALLOWED_SYMBOLS    = "TMF"
$env:MAX_DAILY_LOSS     = "500"
$env:MAX_POSITION_SIZE  = "1"
$env:MAX_DAILY_TRADES   = "1"
$env:MAX_LOSS_PER_TRADE = "150"
$env:MIN_LIVE_CONFIDENCE= "0.85"

$env:LIVE_BROKER_ADAPTER_PATH = "C:\Trading\live-adapters\shioaji\live_adapter.py"
$env:LIVE_ADAPTER_ALLOWLIST   = "C:\Trading\live-adapters"

# CLI:
cd C:\trading\ai_fibo_vision_trader
.\.venv\Scripts\python.exe -m tools.run_live_order `
    --ai-decision decision.json `
    --adapter-source external `
    --json
```

The loader will refuse if:

- the adapter path is inside the main repo;
- the path is not under the allowlist;
- the module has no class named `LiveAdapter`;
- `LiveAdapter` does not satisfy the Protocol (missing `submit_order`,
  `cancel_order`, `positions`, `account_equity`, `connect`,
  `disconnect`).

## TMF (micro-TX) contract resolution

The template enumerates `api.Contracts.Futures` and picks any
contract whose `code` starts with `TMF` or whose `name` contains
`微型台指` / `Mini TX`. It sorts by available date / month field
to pick the nearest expiry. The operator may want to refine this
logic for specific roll behaviour.

## Point value

Each TMF contract: **NT$10 / point / contract**. The main repo's
`ai_swing.tmf_pnl` module hardcodes this constant.

## What this template does NOT do

- Auto-cancel on KillSwitch trip (operator handles manually via
  broker UI -- see `docs/emergency_stop.md`).
- Auto-flatten on session close.
- Multi-leg / OCO orders.
- Real-time market data subscription (Phase 7.B+).
- Order book / depth queries.

These are operator-side extensions.

## Reference

- Shioaji official docs: https://ai.sinotrade.com.tw/python/Main/index.aspx
- Main repo's live adapter contract: `docs/live_adapter_runbook.md`
- Emergency stop procedure: `docs/emergency_stop.md`
- First-trade ritual: `docs/first_real_trade_procedure.md`
