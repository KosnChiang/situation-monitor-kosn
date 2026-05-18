# Scheduler Runbook (Phase 5.F)

> Mock-only operator procedures for running the Phase-5.D watch loop
> and the Phase-5.5 quote feed as Windows Scheduled Tasks. Last
> updated: Phase 5.F handoff. Pinned to commits 9a475c0 (5.B) /
> 639c728 (5.C) / 2e43db4 (5.D) / c205bd4 (5.E).

Companion to `docs/phase5e_watch_loop_runbook.md` — that covers the
foreground PowerShell-wrapper workflow. This document covers the
"register once, runs at logon" Task Scheduler workflow on top of the
same wrappers.

The 5.F scripts compose existing 5.E wrappers. Zero production logic
changes. Eight independent layers protect mock-only-ness
(§ 3 walks through them).

---

## 1. 一鍵安裝（最常用）

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\install_watch_loop_task.ps1
```

What it does (read `scripts/install_watch_loop_task.ps1` for the exact
flow):

1. Pins `LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock`;
   refuses to run if any is already set to anything else.
2. Refuses to run if any broker credential env (`SHIOAJI_API_KEY`,
   `IB_PASSWORD`, `CTPRO_USER`, …) is set.
3. Registers two scheduled tasks under `\AIFiboVisionTrader\`:
   * `watch_loop` — invokes `scripts/watch_loop_run.ps1`
   * `quote_feed` — invokes `scripts/quote_feed_run.ps1`
4. Each task's Action explicitly prefixes the wrapper invocation with
   `$env:LIVE_TRADING='false'; $env:EXECUTION_MODE='mock';
   $env:BROKER_MODE='mock';` — the envelope is pinned in the Task
   Scheduler GUI itself, BEFORE the wrapper's own refuse-if-hot block
   runs.
5. **Tasks are registered Disabled by default.** The operator must
   explicitly enable + start them. This is intentional — the install
   step never leads directly to a running loop.

Expected output (trimmed):

```
[install] registered \AIFiboVisionTrader\watch_loop
[install]   state: Disabled (operator must Enable-ScheduledTask + Start-ScheduledTask)
[install] registered \AIFiboVisionTrader\quote_feed
[install]   state: Disabled (operator must Enable-ScheduledTask + Start-ScheduledTask)

----- install summary -----
  task folder    : \AIFiboVisionTrader\
  default state  : Disabled
  submit         : False
  live_capture   : False

Next steps:
  - Enable: Enable-ScheduledTask -TaskPath '\AIFiboVisionTrader\' -TaskName watch_loop
  - Start:  Start-ScheduledTask  -TaskPath '\AIFiboVisionTrader\' -TaskName watch_loop
  - (do the same for quote_feed before starting watch_loop)
  - Uninstall: .\scripts\uninstall_watch_loop_task.ps1
```

### 安裝變體

```powershell
.\scripts\install_watch_loop_task.ps1 -EnableNow              # enable + start (still no -Submit)
.\scripts\install_watch_loop_task.ps1 -EnableNow -Submit      # opt-in mock fills via MockExecutor
.\scripts\install_watch_loop_task.ps1 -Force                  # overwrite existing tasks
.\scripts\install_watch_loop_task.ps1 -EnableNow -BasePrice 2400 -LoopInterval 10
```

Critical: `-Submit` and `-LiveCapture` are STORED IN THE TASK
DEFINITION. Pass them only after a successful dry-run
(`.\scripts\watch_loop_smoke.ps1`) and after confirming via section 3
that the envelope is correct.

---

## 2. 啟動順序與啟動方式

### 2.1 啟動順序

Quote feed must start BEFORE the watch loop. Otherwise the loop reads
no `last` field and every iteration emits `signal.side="FLAT"`.

```powershell
# Enable + start the quote feed first, then the loop:
Enable-ScheduledTask -TaskPath '\AIFiboVisionTrader\' -TaskName quote_feed
Start-ScheduledTask  -TaskPath '\AIFiboVisionTrader\' -TaskName quote_feed

# Wait a moment for the first quote to land in logs/quotes.jsonl:
Start-Sleep -Seconds 2
Get-Content logs\quotes.jsonl -Tail 1

# Now the loop:
Enable-ScheduledTask -TaskPath '\AIFiboVisionTrader\' -TaskName watch_loop
Start-ScheduledTask  -TaskPath '\AIFiboVisionTrader\' -TaskName watch_loop
```

### 2.2 確認啟動成功

```powershell
.\scripts\watch_loop_status.ps1
```

Expected: `OVERALL : OK` and both tasks `state=Running lastResult=0`.

### 2.3 預設行為（critical reminders）

* `--Submit` is NOT in the task action by default → no MockExecutor
  calls → `logs/trades.jsonl` not appended.
* `--LiveCapture` is NOT in the task action by default → reads
  `logs/capture_test.png` each iteration → no mss → no display
  needed.
* Notify mode is `off` by default in the wrapper → no Telegram even
  if creds are present.

---

## 3. 確認 mock-only（8 層獨立守護）

Run anytime you suspect drift. Each layer is independent.

| Layer | What it checks | How |
|---|---|---|
| 1. Install script | Operator's session env at install time | `install_watch_loop_task.ps1` refuses if hot |
| 2. **Task action command line** | Envelope pinned in the task definition itself | `Get-ScheduledTask -TaskPath '\AIFiboVisionTrader\' -TaskName watch_loop \| Select-Object -ExpandProperty Actions` -- the `Arguments` string must start with `-NoProfile -ExecutionPolicy Bypass -Command "$env:LIVE_TRADING='false'; ...` |
| 3. **Task principal** | Task runs as the current user, RunLevel Limited (NOT LocalSystem / SYSTEM) | `Get-ScheduledTask ... \| Select-Object -ExpandProperty Principal` -- expect `UserId=<your name>`, `LogonType=Interactive`, `RunLevel=Limited` |
| 4. Wrapper script | At task fire time, the wrapper re-pins and refuses if hot | `scripts/watch_loop_run.ps1` / `quote_feed_run.ps1` (Phase 5.E) |
| 5. Python entry | `tools.watch_fibo_loop._refuse_if_live()` and `tools.quote_feed._refuse_if_live()` (Phase 5.D / 5.5) | |
| 6. RiskGate | `risk.risk_gate.RiskGate.__init__` raises `LiveTradingForbidden` | `python -c "from risk.risk_gate import RiskGate; print(RiskGate().__class__.__name__)"` -- expect `RiskGate` |
| 7. MockExecutor | Submit boundary check on `LIVE_TRADING` / `EXECUTION_MODE` | `executor/mock_executor.py` |
| 8. **trades.jsonl audit** | Empirical: no row in `logs/trades.jsonl` has `mode != mock` | `Get-Content logs\trades.jsonl \| Where-Object { ($_ \| ConvertFrom-Json).mode -ne 'mock' }` -- expect NO output. This is the load-bearing check. |

`watch_loop_status.ps1` runs layers 2-8 automatically and exits
non-zero on any violation.

---

## 4. 檢查 logs / status

```powershell
.\scripts\watch_loop_status.ps1
```

Reports 5 sections:

1. `task`    — `state / lastRun / lastResult / nextRun` for both tasks
2. `process` — PID file presence + liveness of those PIDs
3. `jsonl`   — freshness (`seconds since last row`) of
               `watch_loop.jsonl` and `quotes.jsonl`; threshold via
               `-FreshnessWarnSec` (default 60s)
4. `audit`   — `trades.jsonl` row count + count of any row whose
               `mode != mock` (PRODUCTION INCIDENT if non-zero)
5. `tail`    — last 5 rows of `watch_loop.jsonl` pretty-printed
               (side / y / dedupe / submitted)

Exit codes:
* `0` — OK
* `1` — INCIDENT (non-mock row found in `logs/trades.jsonl`)
* `2` — DEGRADED (stale jsonl, dead PID, disabled task, etc.)

Other useful direct queries (no script wrapper needed):

```powershell
# Task action: what command will fire?
Get-ScheduledTask -TaskPath '\AIFiboVisionTrader\' -TaskName watch_loop |
    Select-Object -ExpandProperty Actions | Format-List

# Last 50 rows of watch_loop.jsonl
Get-Content logs\watch_loop.jsonl -Tail 50

# Dedupe action distribution
Get-Content logs\watch_loop.jsonl | ForEach-Object {
    ($_ | ConvertFrom-Json).dedupe.action
} | Group-Object | Sort-Object Count -Descending | Format-Table Count, Name
```

---

## 5. 重啟

```powershell
.\scripts\restart_watch_loop.ps1                       # both
.\scripts\restart_watch_loop.ps1 -OnlyLoop             # watch_loop only
.\scripts\restart_watch_loop.ps1 -OnlyFeed             # quote_feed only
.\scripts\restart_watch_loop.ps1 -TimeoutSec 20        # longer wait for stop
```

Behaviour:
* `Stop-ScheduledTask`, then poll `Get-ScheduledTaskInfo` until
  `state != Running` (max `-TimeoutSec`, default 10s)
* `Start-ScheduledTask`
* Skips Disabled tasks (won't auto-enable; that's an explicit
  operator action)
* Skips Absent tasks (suggests `install_watch_loop_task.ps1`)

For non-task (foreground PowerShell) restart, the Phase-5.E PID-file
workflow is still the right tool:

```powershell
.\scripts\stop_watch_loop.ps1
.\scripts\watch_loop_run.ps1
```

---

## 6. 解除安裝

```powershell
.\scripts\uninstall_watch_loop_task.ps1                # remove both, clean PID files
.\scripts\uninstall_watch_loop_task.ps1 -KeepPidFiles  # leave logs\*.pid.log
```

Idempotent: missing tasks are reported, not errored. Stops Running
tasks before unregistering. Tries to remove the empty
`\AIFiboVisionTrader\` folder.

After uninstall, verify cleanup:

```powershell
Get-ScheduledTask -TaskPath '\AIFiboVisionTrader\*' -ErrorAction SilentlyContinue
# Expected: no output

Get-ChildItem logs\*.pid.log -ErrorAction SilentlyContinue
# Expected (without -KeepPidFiles): no output
```

---

## 7. Rollback / 緊急停機

If `watch_loop_status.ps1` exits with code 1 (non-mock row found in
`trades.jsonl`):

1. **Stop everything immediately**:
   ```powershell
   .\scripts\uninstall_watch_loop_task.ps1   # most decisive: also removes tasks
   .\scripts\stop_watch_loop.ps1             # belt + suspenders
   ```
2. **Re-verify section 3** layers 6-8.
3. **Quarantine the logs** (same procedure as
   `docs/phase5e_watch_loop_runbook.md` § 6).
4. **Capture environment proof**:
   ```powershell
   $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
   Get-ScheduledTask -TaskPath '\AIFiboVisionTrader\*' -ErrorAction SilentlyContinue |
       Format-List | Out-File "logs\tasks.$stamp.txt"
   Get-ChildItem env: | Where-Object Name -match 'LIVE|EXEC|BROKER|SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO' |
       Out-File "logs\envelope.$stamp.txt"
   ```
5. **Do NOT push** any changes until you've understood the root cause.

---

## 8. Operator Checklist（每次 session 跑一次）

Print this section or keep it open in another window.

**Initial install (one-time per machine):**

- [ ] `cd C:\Trading\ai_fibo_vision_trader`
- [ ] `git status` — branch + working tree clean
- [ ] `.\scripts\test.ps1` — full suite green (currently 280+ passing)
- [ ] `$env:LIVE_TRADING, $env:EXECUTION_MODE, $env:BROKER_MODE` — `false / mock / mock` or empty
- [ ] No broker credential env in this shell:
      `Get-ChildItem env: | Where-Object Name -match 'SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO'` — no output
- [ ] `.\scripts\watch_loop_smoke.ps1` — exit 0, `side=LONG`
- [ ] `.\scripts\install_watch_loop_task.ps1` — both tasks Disabled
- [ ] Verify in Task Scheduler GUI: open `Task Scheduler Library →
      AIFiboVisionTrader`. Confirm `watch_loop` and `quote_feed` exist,
      State = Disabled, Actions tab shows the envelope pin
      `$env:LIVE_TRADING='false'; ...`

**Session start:**

- [ ] `.\scripts\watch_loop_status.ps1` — note pre-session state
- [ ] If using Tasks: enable + start quote_feed first, wait 2s,
      then watch_loop
- [ ] If not using Tasks: § 2 of `phase5e_watch_loop_runbook.md`

**Session end:**

- [ ] `.\scripts\restart_watch_loop.ps1 -OnlyLoop` (if you only want to
      cycle the loop) OR `Stop-ScheduledTask` both, OR
      `.\scripts\uninstall_watch_loop_task.ps1` for a full teardown
- [ ] `.\scripts\watch_loop_status.ps1` — `OVERALL : OK` and the
      trades.jsonl audit row says `all mode=mock`
- [ ] `Get-Content logs\trades.jsonl | Where-Object { ($_ |
      ConvertFrom-Json).mode -ne 'mock' }` — no output
- [ ] `git status` — working tree as expected

---

## 9. 故障排除

| Symptom | Likely cause | Fix |
|---|---|---|
| `Refusing to run: LIVE_TRADING=true` from install script | Operator's shell has a hot envelope | Close shell, open fresh PowerShell, retry. Do NOT try to unset in place |
| `Task ... already exists. Pass -Force to overwrite` | Re-running install without cleaning up | Either `.\scripts\uninstall_watch_loop_task.ps1` first, or pass `-Force` to overwrite |
| `Register-ScheduledTask : Access is denied` | UAC requires elevation for this task path | Re-launch PowerShell as Administrator and retry. The tasks themselves still run as the current user (Principal Limited) |
| `Status report shows task state=Ready, lastResult=2147942405` | `0x80070005` = Access denied at runtime. Wrapper or python could not write log | Confirm operator user has write access to the repo's `logs/` directory; re-run `.\scripts\test.ps1` |
| Status report shows `OFF` for a task | Task is Disabled. Run `Enable-ScheduledTask` for it | |
| Status report shows `DEAD` for a process | PID file left behind by a crashed wrapper | `Remove-Item logs\*.pid.log`; investigate the python traceback if any in the wrapper's output |
| Status report shows `STALE` for `quotes.jsonl` | quote_feed task stopped writing | Check `Get-ScheduledTaskInfo` for quote_feed; restart with `.\scripts\restart_watch_loop.ps1 -OnlyFeed` |
| Status report shows `STALE` for `watch_loop.jsonl` | watch_loop task stopped writing (probably crashed) | Same — restart with `-OnlyLoop` and investigate any python traceback |
| Status exit code 1 (INCIDENT) | Non-mock row in `trades.jsonl` | § 7 immediately |
| Task fires but signal is always FLAT | `quotes.jsonl` is stale or absent; or calibration is wrong | `Get-Content logs\quotes.jsonl -Tail 3`; check `config\capture.yaml` calibration block |
| Task action shows the wrong command in GUI | Someone hand-edited the task in Task Scheduler GUI | Reinstall: `.\scripts\uninstall_watch_loop_task.ps1`, then `.\scripts\install_watch_loop_task.ps1` |
| `mss` import error after enabling `-LiveCapture` task | RDP session disconnected or user logged out | Tasks with `-LiveCapture` need an active interactive session. Either stay logged in or revert to offline-image (default) |

---

## 10. 參考連結

* `docs/phase5e_watch_loop_runbook.md` — foreground (PID-based) operator workflow
* `docs/quote_feed_design.md` — Phase-5.5 process-boundary contract (why quote_feed and watch_loop are separate tasks)
* `docs/hermes_operator_runbook.md` — broader Windows envelope
* `scripts/install_watch_loop_task.ps1` — inline docstring covers every parameter
* `tests/test_phase5f_scheduler_scripts.py` — static guards that pin the install script's safety properties
* `tests/test_no_live_trading_phase5f.py` — scoped guards (no broker SDK / LIVE_TRADING / LocalSystem principal / etc.)
