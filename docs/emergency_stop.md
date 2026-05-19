# Emergency Stop Procedure

Three escalating levels of "stop". Pick the one matching the urgency.
Memorise the Level 1 command -- it is the most common.

The codebase never auto-flattens existing positions in v1. Manual
flatten is always operator territory (Level 3).

---

## Level 1 -- Block the next order (5 seconds)

Use when: a future signal looks wrong, market conditions changed, or
the operator needs a coffee.

```powershell
# From any PowerShell, anywhere on the machine:
New-Item -ItemType File -Path C:\trading\ai_fibo_vision_trader\logs\.killswitch -Force
```

What this does:

- The next `tools.dry_run_live_order` invocation pre-checks
  `logs\.killswitch` and exits 4 BEFORE loading the adapter.
- The pipeline-internal `KillSwitch.is_active()` also returns True,
  so the kill switch fires at step 3 of pipeline.run() even if some
  future caller bypasses the CLI pre-check.
- Any open broker positions are **NOT** affected. They continue to
  exist on the broker side. Stops and targets continue to work
  broker-side.

When ready to resume:

```powershell
Remove-Item C:\trading\ai_fibo_vision_trader\logs\.killswitch -ErrorAction SilentlyContinue
```

Do not Remove-Item until the operator is back at the terminal and
ready to monitor the next trade.

---

## Level 2 -- Stop the current session (10 seconds)

Use when: the CLI is mid-execution and the operator wants it to stop
right now (e.g. signal looked wrong only after pressing Enter).

```powershell
# 1. If a CLI is running in this terminal, Ctrl-C.
# 2. If a CLI is running in a different terminal, find and kill:
Get-Process python | Where-Object { $_.MainWindowTitle -like "*dry_run_live_order*" -or $_.Path -like "*ai_fibo_vision_trader*" } | Stop-Process -Force

# 3. Set the env-level kill switch so any new CLI invocation also exits 4:
$env:LIVE_KILL_SWITCH = "1"

# 4. (Optional) Also create the kill file as belt-and-braces:
New-Item -ItemType File -Path C:\trading\ai_fibo_vision_trader\logs\.killswitch -Force
```

What this does:

- Ctrl-C interrupts the active CLI before the broker `submit_order`
  call completes IF Ctrl-C lands before the call starts. If Ctrl-C
  lands during the broker call, the broker may still receive the
  order. Verify on the broker side after stopping.
- The env-level kill is process-scoped: it affects only the current
  terminal's child processes. New terminals don't see it.
- The file-level kill is machine-scoped: it affects every Python
  invocation that uses this kill path.

Existing broker-side positions are still untouched.

---

## Level 3 -- Manual flatten (30+ seconds)

Use when: a position is open and must be closed immediately,
regardless of the bot's view.

The bot does NOT close positions in v1. Operator must use the
broker's UI:

1. Open the broker's web or desktop app.
2. Locate the open position(s) for the relevant symbol.
3. Place a market order in the opposite direction at full size, or
   use the broker's "close position" shortcut.
4. Wait for the close confirmation.
5. Cross-check the broker's P&L against `logs\live_fills.jsonl`:

```powershell
# Show the last 10 fills (each line is one fill record):
Get-Content C:\trading\ai_fibo_vision_trader\logs\live_fills.jsonl -Tail 10
```

6. Write an incident note. Recommended location:
   `C:\trading\ai_fibo_vision_trader\logs\incident_<UTC_date>.md`.
   Include:
   - What signal triggered the order.
   - What the AI decision JSON said.
   - What the actual broker fill was (price, slippage, time).
   - What went wrong and what the operator did.
   - Was the kill switch invoked? At which level?

This file is gitignored along with the rest of `logs\`; it stays
local.

---

## Verifying the kill switch is honored

After invoking Level 1 or Level 2, run one dry CLI to confirm:

```powershell
$env:LIVE_TRADING       = "true"
$env:EXECUTION_MODE     = "live"
$env:FAKE_LIVE_ADAPTER  = "true"
$env:LIVE_READY_FLAG    = "approved-" + (Get-Date -AsUTC -Format "yyyyMMdd")
$env:LIVE_TOKEN_HMAC    = "verify"
$env:ALLOWED_SYMBOLS    = "XAUUSD"
$env:MAX_DAILY_LOSS     = "50"
$env:MAX_POSITION_SIZE  = "1"

# A trivial decision file:
'{"decision_id":"verify-kill","symbol":"XAUUSD","side":"LONG","entry":1,"stop":0.5,"target":1.5,"confidence":0.9,"reason":"verify"}' | Out-File -Encoding utf8 verify.json

# Expect exit 4 and outcome="killed" in the JSON output:
.\.venv\Scripts\python.exe -m tools.dry_run_live_order --ai-decision verify.json --json --kill-file C:\trading\ai_fibo_vision_trader\logs\.killswitch
```

If exit is NOT 4, the kill is not honored -- escalate to Level 3 and
file an incident note.

---

## What is explicitly NOT done in v1

- Adapter-level heartbeat (continuous broker connectivity check).
- Auto-flatten on KillSwitch trip.
- Auto-cancel of in-flight broker orders.
- Email / SMS / Telegram page-out on auto-trip.
- Multi-strategy fan-out (one strategy at a time).

These are roadmap items, not safety bugs. The current envelope's
guarantee is "no NEW orders past the kill"; closing existing
positions is operator's hands-on.

---

## When to escalate from Level 1 to higher

| Symptom | Escalate to |
|---|---|
| Operator wants to skip next trade | Level 1 |
| CLI is currently running and operator changed mind | Level 2 |
| Position is open and operator wants out NOW | Level 3 |
| Broker fill log shows a position the operator did not approve | Level 3 + incident note |
| `account_equity()` returns unexpected value | Level 2, then check broker UI |
| Multiple errors in `logs\live_rejections.jsonl` in short time | Level 1, investigate |
| Kill file removal fails | Level 3 + incident note + reboot |

---

## Phone-numbers / addresses (operator fills in)

| Resource | Contact |
|---|---|
| Broker emergency hotline | __________________ |
| Broker live chat URL | __________________ |
| Broker account number | __________________ |
| Operator phone | __________________ |
| Operator backup phone | __________________ |
| Internet connection backup (mobile hotspot SSID/password) | __________________ |

Print this page and keep it within arm's reach during any live
session.
