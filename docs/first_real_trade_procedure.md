# First Real Trade Procedure

Step-by-step ritual for the operator's first real-money order via
this codebase.

Read this entirely before T-1. Print it. Tick each step as you go.

Do not skip steps. Do not reorder. Each step has a reason; the reasons
are documented in `docs/live_adapter_runbook.md` and
`docs/micro_live_readiness_checklist.md`.

---

## T-1 day -- preparation

Time required: 30 to 60 minutes.

- [ ] Run `scripts\test.ps1`. Confirm: ______ passed, 0 failed.
- [ ] Verify `git status -sb` is clean (no unexpected changes; ahead/behind
      is 0). Note the current commit hash: ______________________.
- [ ] Walk through `docs/live_adapter_runbook.md` sections 1 through 4.
      Confirm your out-of-tree adapter satisfies every contract in
      section 2 and stores credentials per section 4.
- [ ] Run the adapter's own paper-account smoke test
      (`tests/test_adapter_against_paper.py` in the adapter's repo).
      Confirm: ______ orders simulated, 0 hangs, 0 unhandled exceptions.
- [ ] Open the broker's web/desktop app. Log in. Verify the account
      balance and the symbol's live quote.
- [ ] Print `docs/micro_live_readiness_checklist.md` and
      `docs/emergency_stop.md`. Keep them within arm's reach.
- [ ] Set up a clean PowerShell session for tomorrow: close other
      terminals to avoid leaked env vars.
- [ ] Sleep on it. If anything feels rushed, defer.

---

## T-0 morning -- env setup

Time required: 10 to 15 minutes.

Open a fresh PowerShell window. Do this exactly once at start of day.

```powershell
# Navigate to the project root.
cd C:\trading\ai_fibo_vision_trader

# Set every LiveUnlockGate condition explicitly. Do NOT rely on
# leftover env from prior sessions.
$env:LIVE_TRADING       = "true"
$env:EXECUTION_MODE     = "live"
$env:LIVE_READY_FLAG    = "approved-" + (Get-Date -AsUTC -Format "yyyyMMdd")
$env:LIVE_TOKEN_HMAC    = -join ((48..57 + 97..102) | Get-Random -Count 32 | ForEach-Object {[char]$_})

# Burn-in risk parameters.
$env:ALLOWED_SYMBOLS    = "XAUUSD"   # one symbol only
$env:MAX_DAILY_LOSS     = "50"        # $$ at 0.5% of $10k acct
$env:MAX_POSITION_SIZE  = "1"
$env:MAX_DAILY_TRADES   = "1"
$env:MAX_LOSS_PER_TRADE = "10"        # $$ at 0.1% of $10k acct
$env:MIN_LIVE_CONFIDENCE= "0.85"

# Adapter source. For the very first real trade, ONE more rehearsal
# against fake is recommended before flipping to external:
$env:FAKE_LIVE_ADAPTER  = "true"
# When ready to go live with the real adapter:
# Remove-Item Env:FAKE_LIVE_ADAPTER
# $env:LIVE_BROKER_ADAPTER_PATH = "C:\Trading\live-adapters\<broker>\live_adapter.py"
# $env:LIVE_ADAPTER_ALLOWLIST   = "C:\Trading\live-adapters"

# Verify no stale kill switch.
if (Test-Path logs\.killswitch) { Remove-Item logs\.killswitch -Force }

# Verify keychain has all required credentials (run from adapter venv,
# not this terminal). If your adapter throws on missing credential,
# the next CLI invocation will surface it immediately.

# Verify env is consistent.
Get-ChildItem Env:LIVE_*, Env:MAX_*, Env:ALLOWED_*, Env:MIN_*, Env:FAKE_*
```

- [ ] All env values printed above are correct.
- [ ] `LIVE_TOKEN_HMAC` was randomly generated this morning (do NOT
      reuse yesterday's value).
- [ ] `logs\.killswitch` does NOT exist.
- [ ] `ALLOWED_SYMBOLS` contains exactly one symbol.

---

## T-0 fake rehearsal -- one more time

Before flipping to the real adapter, do one final fake-mode end-to-end.

```powershell
# Manual decision payload for rehearsal. Use a decision_id you will
# NOT use for the real trade.
'{"decision_id":"rehearsal-1","symbol":"XAUUSD","side":"LONG","entry":23010.5,"stop":22995.0,"target":23040.0,"confidence":0.88,"reason":"rehearsal","invalidation":"close below stop","data_sources":["manual"],"mode":"live"}' | Out-File -Encoding utf8 rehearsal.json

.\.venv\Scripts\python.exe -m tools.dry_run_live_order `
  --ai-decision rehearsal.json `
  --qty 1.0 `
  --daily-trade-count 0 `
  --daily-loss 0.0 `
  --adapter-source fake `
  --json
```

- [ ] Exit code: 0
- [ ] Outcome: filled
- [ ] `logs\live_orders.jsonl` tail shows the rehearsal order with
      `mode: "live"`.
- [ ] `logs\live_fills.jsonl` tail shows the matching fill, same
      `order_id`.
- [ ] `logs\live_adapter_loads.jsonl` either does not exist or has
      NOT been appended (fake mode never invokes the loader).

If any of these is wrong, **STOP**. Investigate before flipping to
the real adapter.

Delete the rehearsal decision so it cannot be confused with the real
one:

```powershell
Remove-Item rehearsal.json
```

---

## T-0 flip to external adapter

When the rehearsal is clean and the operator is ready for real money:

```powershell
Remove-Item Env:FAKE_LIVE_ADAPTER
$env:LIVE_BROKER_ADAPTER_PATH = "C:\Trading\live-adapters\<broker>\live_adapter.py"
$env:LIVE_ADAPTER_ALLOWLIST   = "C:\Trading\live-adapters"
```

- [ ] `FAKE_LIVE_ADAPTER` is no longer in env (verify with
      `Get-ChildItem Env:FAKE_LIVE_ADAPTER` -- should error / be empty).
- [ ] `LIVE_BROKER_ADAPTER_PATH` points to an existing file.
- [ ] `LIVE_ADAPTER_ALLOWLIST` includes the adapter's directory.

---

## T-0 trade time -- the one trade

Sit at the terminal. Have the broker UI open in a second window.

1. The AI (Hermes / Claude / strategy script) generates a decision
   JSON. Save it as `decision.json`.

2. **Read the decision aloud** before doing anything else:
   "I am about to enter __________ (LONG/SHORT) on __________ (symbol)
   at __________ (entry). My stop is __________. My target is __________.
   My confidence is __________. If stopped, I lose __________ dollars."

3. Cross-check against broker UI:
   - Current mid price within 0.5% of `entry`? __________
   - Symbol matches `ALLOWED_SYMBOLS`? __________
   - Direction makes sense (LONG when chart looks bullish, etc.)? __________

4. If anything looks off, **STOP**. Do not submit. Investigate.

5. If everything checks out, submit:

```powershell
.\.venv\Scripts\python.exe -m tools.dry_run_live_order `
  --ai-decision decision.json `
  --qty 1.0 `
  --daily-trade-count 0 `
  --daily-loss 0.0 `
  --adapter-source external `
  --operator-equity <your account balance in dollars> `
  --json
```

6. Watch the JSON output:
   - `outcome: "filled"` and `exit_code: 0` -- order placed.
   - Any other outcome -- order rejected; investigate the layer.

7. Within 5 seconds, verify on the broker UI:
   - A new position exists for the expected symbol, side, and size.
   - The fill price in the broker UI matches `fill_price` in
     `logs\live_fills.jsonl` (within reasonable slippage).
   - The broker shows pending stop and target if you placed them
     broker-side.

If broker UI and `logs\live_fills.jsonl` disagree:
**Level 2 emergency stop** and reconcile manually.

---

## T+during -- monitoring

- [ ] The operator stays at the terminal until the trade closes (stop,
      target, or manual).
- [ ] The operator does NOT submit a second trade today even if a
      second signal arrives. `MAX_DAILY_TRADES=1` for burn-in.
- [ ] The operator does NOT change env vars mid-session.
- [ ] If anything looks wrong: emergency stop (see
      `docs/emergency_stop.md`).

---

## T+post -- after the trade closes

Within 30 minutes of position close:

1. Record actual vs expected:

```
Symbol:                __________
Side:                  __________
Predicted entry:       __________
Actual fill:           __________
Slippage:              __________ (actual - predicted)
Predicted stop:        __________
Predicted target:      __________
Actual exit:           __________
Close reason:          stopped / targeted / manual / other
Predicted P&L:         __________
Actual P&L:            __________
Hold time:             __________ minutes
```

2. Reconcile with `logs\live_orders.jsonl` and `logs\live_fills.jsonl`
   for the trade's `order_id`. Confirm the audit trail is complete.

3. Reconcile with the broker UI's trade history. Confirm the same
   trade appears there.

4. Write a post-trade review in
   `C:\trading\ai_fibo_vision_trader\logs\post_trade_review_<UTC_date>.md`:

```markdown
# Post-Trade Review YYYY-MM-DD

## Decision
- decision_id: ____
- AI confidence: ____
- AI reason: ____

## Outcome
- Outcome: filled / rejected (layer ____)
- Predicted vs actual: ____
- Slippage: ____
- P&L: ____

## Lessons
- What surprised me:
- What worked:
- What to adjust:

## Decision: continue tomorrow?
- yes / no, and why
```

5. Clear today's env to avoid leaks into tomorrow's session:

```powershell
foreach ($n in @(
    "LIVE_TRADING","EXECUTION_MODE","LIVE_READY_FLAG","LIVE_TOKEN_HMAC",
    "ALLOWED_SYMBOLS","MAX_DAILY_LOSS","MAX_POSITION_SIZE","MAX_DAILY_TRADES",
    "MAX_LOSS_PER_TRADE","MIN_LIVE_CONFIDENCE","FAKE_LIVE_ADAPTER",
    "LIVE_BROKER_ADAPTER_PATH","LIVE_ADAPTER_ALLOWLIST","LIVE_KILL_SWITCH"
)) {
    Remove-Item "Env:$n" -ErrorAction SilentlyContinue
}
```

6. Tomorrow morning, repeat from "T-0 morning -- env setup" with a
   fresh `LIVE_TOKEN_HMAC` and a fresh `LIVE_READY_FLAG` date.

---

## If today's trade was a loss

This is expected. The point of the burn-in 10 trades is to surface
problems, not to make money.

- [ ] Did the loss equal the `MAX_LOSS_PER_TRADE` you set? If larger,
      something is wrong; investigate slippage or stop placement.
- [ ] Did `KillSwitch` auto-trip (record_loss / consecutive losses)?
      If yes, do NOT trade tomorrow until the operator manually
      assesses.
- [ ] Was the broker UI stop hit at the expected price? If broker
      filled the stop far from the configured level, raise it with
      the broker.

---

## If today's trade was a win

- [ ] Resist the urge to "go again". `MAX_DAILY_TRADES=1` for burn-in.
- [ ] Write the post-trade review with the same diligence as a loss.
- [ ] Do not relax risk limits today.
- [ ] Sleep on tomorrow's plan.

---

## When to graduate from burn-in

After 10 real trades meeting all of:

- 0 failed kill-switch trips
- 0 broker-side surprises (unexpected fills, hangs, disconnects)
- Predicted P&L within +/- 50% of actual P&L (slippage and timing make
  this loose; tighter convergence is better)
- 0 incidents requiring Level 3 emergency stop

...the operator may consider relaxing to `MAX_DAILY_TRADES=3`,
`MAX_LOSS_PER_TRADE=0.2%`, two symbols. Every relaxation requires
re-signing `docs/micro_live_readiness_checklist.md`.

Hard caps that never relax: `MAX_DAILY_LOSS <= 2%`,
`MIN_LIVE_CONFIDENCE >= 0.7`.
