# Watch Loop Operator Runbook (Phase 5.E)

> Mock-only operator procedures for the Fibo CV watch loop on the
> Windows trading box. Last updated: Phase 5.E handoff. Pinned to
> commits 9a475c0 (Phase 5.B filter) / 639c728 (Phase 5.C latest_quote
> + calibration) / 2e43db4 (Phase 5.D watch loop).

This document covers day-to-day operation of the loop. The CLI and
filter design lives in inline docstrings and `docs/quote_feed_design.md`;
the broader Hermes / Windows envelope is in
`docs/hermes_operator_runbook.md`.

---

## 1. 一鍵 dry-run smoke（最常用）

Use this every fresh checkout, every reboot, and before any `-Submit`
session.

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\watch_loop_smoke.ps1
```

What it does (read `scripts/watch_loop_smoke.ps1` for the exact flow):

1. Pins `LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock`;
   refuses to run if any of those are already set to anything else.
2. Refuses to run if any broker credential env (`SHIOAJI_API_KEY`,
   `IB_PASSWORD`, `CTPRO_USER`, …) is set.
3. Writes one synthetic quote (default `last=$2250.0`) to
   `logs/quotes_smoke.jsonl`.
4. Isolates `$env:TRADES_LOG = logs/trades_smoke.jsonl` so the real
   `logs/trades.jsonl` is never touched, even with `-Submit`.
5. Runs `python -m tools.watch_fibo_loop` against
   `logs/capture_test.png` + `tests/fixtures/capture_calibration_demo.yaml`
   for N iterations (default 3) with `--interval 0`.
6. Prints a one-line summary per iteration: `side / fibo_y / dedupe /
   submitted`.

Expected output:

```
3  side=LONG  y= 1533  conf=0.90  dedupe=fired                  submitted=False
3  side=LONG  y= 1533  conf=0.90  dedupe=suppressed_same_key    submitted=False
3  side=LONG  y= 1533  conf=0.90  dedupe=suppressed_same_key    submitted=False
logs\watch_loop_smoke.jsonl -- 3 row(s)
logs\trades_smoke.jsonl -- not created (expected when -Submit is omitted)
```

Exit code `0` is success. Anything else: read the python traceback in
the script's output and check § 7 故障排除.

Opt-in mock fill smoke:

```powershell
.\scripts\watch_loop_smoke.ps1 -Submit
# logs/trades_smoke.jsonl will contain exactly 1 row with "mode":"mock"
# (iters 2-3 are dedupe-suppressed and don't append).
```

---

## 2. 兩個 terminal 啟動順序

Quote feed must start BEFORE the watch loop. Otherwise the loop reads
no `last` field and every iteration emits `signal.side="FLAT"` with
`quote.status="file_missing"`.

### 2.1 Terminal A: mock quote feed

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\quote_feed_run.ps1 -Symbol XAUUSD -BasePrice 2250 -Interval 1
```

Leave this terminal running. Expected output (1 line per fetch):

```
[quote_feed] #1 XAUUSD bid=2249.85 ask=2250.35 last=2250.10 source=mock
[quote_feed] #2 XAUUSD bid=2250.02 ask=2250.52 last=2250.27 source=mock
...
```

`-BasePrice 2250` aligns with the Phase-5.C demo calibration so the
walk hangs around the filtered Fibo line at `pixel_y=1533`. If your
real `config/capture.yaml` calibration is filled in, pick a base price
that matches whatever your TradingView chart's filtered Fibo line
maps to.

### 2.2 Terminal B: watch loop

Confirm Terminal A has printed at least one `[quote_feed] #1 …` line
before starting Terminal B. Then:

```powershell
cd C:\Trading\ai_fibo_vision_trader
# Dry run first -- no MockExecutor calls, just records each iteration:
.\scripts\watch_loop_run.ps1 -Interval 5 -MaxIterations 10

# Once dry-run output looks right, opt in to mock fills:
.\scripts\watch_loop_run.ps1 -Interval 5 -MaxIterations 10 -Submit
```

Expected output (1 line per iteration):

```
[loop] #1 signal=LONG y=1533 conf=0.90 submitted=True  dedupe=fired
[loop] #2 signal=LONG y=1533 conf=0.90 submitted=False dedupe=suppressed_same_key
...
```

Defaults: 5-second interval, infinite iterations (Ctrl+C to stop, or
use § 5), 60-second dedupe cooldown, notify mode `off`.

---

## 3. 確認 mock-only（3 層獨立驗證）

Run this anytime you suspect the envelope drifted. All three checks
must pass.

### Layer 1 — PowerShell session env

```powershell
$env:LIVE_TRADING, $env:EXECUTION_MODE, $env:BROKER_MODE
```

Expected output:

```
false
mock
mock
```

If any is empty, the operator scripts will pin them on next run. If
any is anything else (especially `true` / `live`), STOP — close this
shell, open a fresh PowerShell, and try again. Do not try to fix in
place; the leakage source may be a `profile.ps1` you don't control.

### Layer 2 — Python boundary

```powershell
.\.venv\Scripts\python.exe -c "from risk.risk_gate import RiskGate; print(RiskGate().__class__.__name__)"
```

Expected output: `RiskGate`. If you instead see
`LiveTradingForbidden`, layer 1 has drifted; fix that first.

### Layer 3 — trades.jsonl audit

```powershell
Get-Content logs\trades.jsonl | Where-Object { ($_ | ConvertFrom-Json).mode -ne 'mock' }
```

Expected output: **nothing** (no rows). Any output here means a
non-mock fill made it to the log; treat it as a P0 incident, stop the
loop (§ 5), and review.

---

## 4. 檢查 logs

All three files are append-only JSONL. The repo `.gitignore` already
excludes them.

### 4.1 logs/watch_loop.jsonl

One row per loop iteration. Required top-level keys are pinned by
`tests/fixtures/watch_loop_row_schema.json`.

Tail and pretty-print the last 10 iterations:

```powershell
Get-Content logs\watch_loop.jsonl -Tail 10 | ForEach-Object {
    $r = $_ | ConvertFrom-Json
    "{0,4} {1,-5} y={2,5} conf={3:N2} dedupe={4,-24} submitted={5}" -f `
        $r.iter, $r.signal.side, $r.signal.fibo_line_y, $r.signal.confidence, `
        $r.dedupe.action, $r.execution.submitted
}
```

Group by dedupe.action (sanity check on suppression rate):

```powershell
Get-Content logs\watch_loop.jsonl | ForEach-Object {
    ($_ | ConvertFrom-Json).dedupe.action
} | Group-Object | Sort-Object Count -Descending | Format-Table Count, Name
```

Find iterations with capture / quote errors (loop health):

```powershell
Get-Content logs\watch_loop.jsonl | Where-Object {
    $r = $_ | ConvertFrom-Json
    ($r.capture.PSObject.Properties.Name -contains 'error') -or
    ($r.quote.status -ne 'ok')
}
```

### 4.2 logs/trades.jsonl

Only written when `-Submit` is set AND the iteration fires (not
dedupe-suppressed). Every row MUST have `"mode":"mock"` — see § 3
layer 3.

```powershell
# Last 5 mock fills:
Get-Content logs\trades.jsonl -Tail 5

# Total fills today:
Get-Content logs\trades.jsonl | Measure-Object -Line
```

### 4.3 logs/quotes.jsonl

Written by `quote_feed_run.ps1` (Terminal A). Append-only; do not
truncate while the loop is running.

```powershell
Get-Content logs\quotes.jsonl -Tail 3
(Get-Content logs\quotes.jsonl | Measure-Object -Line).Lines
```

---

## 5. 停止 loop

Preferred path: use the stop script. Reads PID files written by
`watch_loop_run.ps1` and `quote_feed_run.ps1` (under
`logs/*.pid.log`).

```powershell
# Stop both watch loop and quote feed:
.\scripts\stop_watch_loop.ps1

# Stop only the watch loop (leave quote feed running):
.\scripts\stop_watch_loop.ps1 -OnlyLoop

# Stop only the quote feed:
.\scripts\stop_watch_loop.ps1 -OnlyFeed
```

Fallback (PID file lost or operator started python by hand):

```powershell
# Find candidate processes; the stop script reports but does NOT kill in fallback mode.
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -match 'watch_fibo_loop|tools\.quote_feed' } |
    Select-Object ProcessId, CommandLine

# Kill by PID once verified:
Stop-Process -Id <pid> -Force
```

Ctrl+C in the foreground terminal also works — the loop has its own
`KeyboardInterrupt` handler that prints `[loop] interrupted after N
iterations` and exits cleanly, and the wrapper's `try/finally` clears
the PID file.

---

## 6. Rollback / 緊急停機

If something looks wrong (non-mock row in `logs/trades.jsonl`, real
order placed, broker credential env leaked):

1. **Stop everything immediately:**
   ```powershell
   .\scripts\stop_watch_loop.ps1
   ```
2. **Re-verify the 3 mock-only layers (§ 3).** Layer 3 (trades.jsonl
   audit) is the load-bearing check.
3. **Quarantine the logs:**
   ```powershell
   $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
   Move-Item logs\watch_loop.jsonl  "logs\watch_loop.$stamp.jsonl.quarantine"
   Move-Item logs\trades.jsonl      "logs\trades.$stamp.jsonl.quarantine"
   ```
4. **Capture environment proof:**
   ```powershell
   Get-ChildItem env: | Where-Object Name -match 'LIVE|EXEC|BROKER|SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO' |
       Out-File "logs\envelope.$stamp.txt"
   ```
5. **Do NOT push** any changes until you've understood the root cause.
6. **Re-run `.\scripts\test.ps1`** — 224+ passing means the static
   guards are still healthy. If any guard fails, that is the priority.

---

## 7. 故障排除

| Symptom | Likely cause | Fix |
|---|---|---|
| `Refusing to run: LIVE_TRADING=true` | A prior command in this shell set the env var | Close shell, open fresh PowerShell; do NOT try to unset in place |
| Every iteration `signal=FLAT reason="no fibo lines kept after filter"` | `logs/capture_test.png` is stale or the chart geometry changed | Re-run `python -m tools.capture_test` and inspect; consider lowering `--min-length-ratio` |
| Every iteration `quote.status="file_missing"` | Terminal A (quote feed) not running or wrote elsewhere | Start `quote_feed_run.ps1` first; confirm `logs/quotes.jsonl` is being appended |
| Every iteration `quote.status="empty"` | Quote feed started but hasn't fetched yet | Wait for Terminal A to print `[quote_feed] #1 …` |
| `calibration.status="invalid"` | `config/capture.yaml` has an inverted axis or bad values | Re-run `tools.calibrate_chart` to validate the 4 numbers; § 5.A of repo history |
| `calibration.status="missing_config"` | `config/capture.yaml` absent | Restore from git; loop will fall back to raw price-as-pixel-y in the meantime |
| `mss` / display error on `-LiveCapture` | No display attached or RDP session detached | Drop `-LiveCapture` and use the default offline path |
| Submit fires every iteration | Dedupe cooldown set too low or key changing between iterations | Check `dedupe.key` is stable; raise `-DedupeCooldown` |
| `stop_watch_loop.ps1` reports "no matching python process" | Loop crashed cleanly and removed its own PID file | Nothing to do |
| Pytest suite drops below 224 | A guard fired | Read the failing test name — Phase 5.B / 5.C / 5.D / 5.E guards each enumerate their scope |

---

## 8. Operator Checklist（每次 session 開頭 / 結束跑一次）

Print this section or keep it open in another window.

**Session start:**

- [ ] `cd C:\Trading\ai_fibo_vision_trader`
- [ ] `git status` — confirm branch and no unexpected untracked files
- [ ] `.\scripts\test.ps1` — full suite green (currently 224+ passing)
- [ ] `$env:LIVE_TRADING, $env:EXECUTION_MODE, $env:BROKER_MODE` — `false / mock / mock` or empty
- [ ] No broker credential env in this shell: `Get-ChildItem env: | Where-Object Name -match 'SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO'` returns nothing
- [ ] `.\scripts\watch_loop_smoke.ps1` — exit 0, `side=LONG`, at least one row in `logs/watch_loop_smoke.jsonl`
- [ ] (optional) Terminal A: `.\scripts\quote_feed_run.ps1 -BasePrice 2250`
- [ ] (optional) Terminal B: `.\scripts\watch_loop_run.ps1` (dry first, then `-Submit` only after dry looks right)

**Session end:**

- [ ] `.\scripts\stop_watch_loop.ps1`
- [ ] § 3 layer 3 — `Get-Content logs\trades.jsonl | Where-Object { ($_ | ConvertFrom-Json).mode -ne 'mock' }` — no output
- [ ] Delete smoke leftovers if desired: `Remove-Item logs\quotes_smoke.jsonl, logs\watch_loop_smoke.jsonl, logs\trades_smoke.jsonl -ErrorAction SilentlyContinue`
- [ ] PID files cleaned: `Get-ChildItem logs\*.pid.log` returns nothing
- [ ] `git status` — working tree as expected, no surprise modifications

---

## 9. 參考連結

- `docs/quote_feed_design.md` — Phase-5.5 quote feed design (process boundary, JSONL contract)
- `docs/hermes_operator_runbook.md` — broader Windows envelope and Hermes operator procedures
- `tools/watch_fibo_loop.py` — the loop CLI; inline docstring covers every flag
- `tests/test_watch_fibo_loop.py` — 21 tests covering the failure modes referenced in § 7
- `tests/test_no_live_trading_phase5d.py` — AST guards that pin `--submit` and `--notify-mode live` to lazy-import paths
- `tests/fixtures/watch_loop_row_schema.json` — required top-level keys for `logs/watch_loop.jsonl`
