# ============================================================
# scripts/watch_loop_status.ps1 -- integrated health check for the
# Phase-5.F scheduled tasks + Phase-5.D loop runtime + Phase-5
# trading core audit.
#
# Reports 5 sections:
#   1. task        -- Get-ScheduledTask + Get-ScheduledTaskInfo for
#                     both \AIFiboVisionTrader\watch_loop and
#                     \AIFiboVisionTrader\quote_feed
#   2. process     -- liveness of PIDs in logs/watch_loop.pid.log
#                     and logs/quote_feed.pid.log
#   3. jsonl       -- freshness (seconds since last row) of
#                     logs/watch_loop.jsonl and logs/quotes.jsonl
#   4. audit       -- logs/trades.jsonl row count + count of any
#                     row whose mode != mock. PRODUCTION INCIDENT
#                     if non-zero.
#   5. tail        -- last 5 rows of logs/watch_loop.jsonl pretty
#                     printed (side / y / dedupe / submitted)
#
# Exit codes:
#   0 - all green
#   1 - audit found a non-mock row in trades.jsonl (P0)
#   2 - one or more health checks degraded (stale jsonl, task in
#       failed state, dead PID), but no mock-only violation
#
# Does NOT pin the mock envelope -- read-only inspection only.
# ============================================================

param(
    [int]$FreshnessWarnSec = 60
)

$ErrorActionPreference = "Continue"  # don't blow up on missing files

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

$taskFolder = "\AIFiboVisionTrader\"
$degraded = 0
$incident = 0

function Write-Row {
    param([string]$Section, [string]$Check, [string]$Status, [string]$Detail)
    "{0,-10} {1,-32} {2,-7} {3}" -f $Section, $Check, $Status, $Detail
}

Write-Host "watch_loop_status.ps1 -- Phase 5.F health check"
Write-Host "  projectRoot : $projectRoot"
Write-Host "  taskFolder  : $taskFolder"
Write-Host "  freshness warn threshold : ${FreshnessWarnSec}s"
Write-Host ""
Write-Host ("{0,-10} {1,-32} {2,-7} {3}" -f "SECTION", "CHECK", "STATUS", "DETAIL")
Write-Host ("{0,-10} {1,-32} {2,-7} {3}" -f "-------", "-----", "------", "------")

# ----- 1. task -------------------------------------------------------------
foreach ($name in @("watch_loop", "quote_feed")) {
    $task = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Row "task" $name "ABSENT" "not registered"
        $degraded++
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
    $state  = $task.State
    $lastRun = if ($info -and $info.LastRunTime) { $info.LastRunTime } else { '(never)' }
    $lastResult = if ($info) { $info.LastTaskResult } else { 'n/a' }
    $nextRun = if ($info -and $info.NextRunTime) { $info.NextRunTime } else { '(none)' }

    $status = "OK"
    if ($state -eq "Disabled") { $status = "OFF"; $degraded++ }
    if ($lastResult -ne 0 -and $lastResult -ne $null -and $lastResult -ne 'n/a' -and $lastResult -ne 267011) {
        # 267011 = SCHED_E_TASK_NOT_READY (acceptable if never run)
        $status = "WARN"; $degraded++
    }
    Write-Row "task" $name $status "state=$state lastRun=$lastRun lastResult=$lastResult nextRun=$nextRun"
}

# ----- 2. process ---------------------------------------------------------
foreach ($entry in @(
    @{ Name = "watch_loop"; File = "logs\watch_loop.pid.log" },
    @{ Name = "quote_feed"; File = "logs\quote_feed.pid.log" }
)) {
    $name = $entry.Name
    $file = $entry.File
    if (-not (Test-Path $file)) {
        Write-Row "process" $name "ABSENT" "no PID file ($file)"
        continue
    }
    $pidValue = (Get-Content $file -Raw).Trim()
    $pidInt = 0
    if (-not [int]::TryParse($pidValue, [ref]$pidInt)) {
        Write-Row "process" $name "WARN" "PID file contains non-integer '$pidValue'"
        $degraded++
        continue
    }
    $proc = Get-Process -Id $pidInt -ErrorAction SilentlyContinue
    if ($proc) {
        $started = $proc.StartTime
        Write-Row "process" $name "ALIVE" "pid=$pidInt started=$started"
    } else {
        Write-Row "process" $name "DEAD" "pid=$pidInt no longer running"
        $degraded++
    }
}

# ----- 3. jsonl freshness -------------------------------------------------
function Test-JsonlFresh {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path $Path)) {
        Write-Row "jsonl" $Label "ABSENT" "$Path not found"
        return
    }
    $lastLine = Get-Content $Path -Tail 1
    if (-not $lastLine) {
        Write-Row "jsonl" $Label "EMPTY" "$Path has 0 rows"
        $script:degraded++
        return
    }
    try {
        $r = $lastLine | ConvertFrom-Json
    } catch {
        Write-Row "jsonl" $Label "WARN" "last row not JSON: $($_.Exception.Message)"
        $script:degraded++
        return
    }
    $rowTs = [double]$r.ts
    $nowTs = [double](Get-Date -UFormat %s)
    $age = [math]::Round($nowTs - $rowTs, 1)
    $status = if ($age -gt $FreshnessWarnSec) { $script:degraded++; "STALE" } else { "OK" }
    Write-Row "jsonl" $Label $status "last row ${age}s ago"
}
Test-JsonlFresh -Path "logs\watch_loop.jsonl" -Label "watch_loop.jsonl"
Test-JsonlFresh -Path "logs\quotes.jsonl"     -Label "quotes.jsonl"

# ----- 4. trades.jsonl mode=mock audit (the load-bearing check) ----------
if (-not (Test-Path "logs\trades.jsonl")) {
    Write-Row "audit" "trades.jsonl mode=mock" "ABSENT" "logs\trades.jsonl not present (no mock fills yet)"
} else {
    $rows = Get-Content "logs\trades.jsonl"
    $rowCount = $rows.Count
    $nonMockRows = @()
    foreach ($line in $rows) {
        try {
            $r = $line | ConvertFrom-Json
            if ($r.mode -ne 'mock') { $nonMockRows += $line }
        } catch {
            $nonMockRows += $line  # treat parse failures as suspicious
        }
    }
    if ($nonMockRows.Count -gt 0) {
        Write-Row "audit" "trades.jsonl mode=mock" "FAIL" "$($nonMockRows.Count) of $rowCount rows are NOT mock -- PRODUCTION INCIDENT"
        Write-Host ""
        Write-Host "non-mock rows:"
        $nonMockRows | ForEach-Object { Write-Host "  $_" }
        $incident++
    } else {
        Write-Row "audit" "trades.jsonl mode=mock" "OK" "$rowCount rows, all mode=mock"
    }
}

# ----- 5. tail watch_loop.jsonl ------------------------------------------
Write-Host ""
Write-Host "----- last 5 rows of logs\watch_loop.jsonl -----"
if (Test-Path "logs\watch_loop.jsonl") {
    Get-Content "logs\watch_loop.jsonl" -Tail 5 | ForEach-Object {
        $r = $_ | ConvertFrom-Json
        $sub = if ($r.execution) { $r.execution.submitted } else { $false }
        "{0,4}  side={1,-5} y={2,5}  conf={3:N2}  dedupe={4,-24} submitted={5}" -f `
            $r.iter, $r.signal.side, $r.signal.fibo_line_y, $r.signal.confidence, `
            $r.dedupe.action, $sub
    }
} else {
    Write-Host "  (not present)"
}

# ----- exit ---------------------------------------------------------------
Write-Host ""
Write-Host "----- status summary -----"
if ($incident -gt 0) {
    Write-Host "OVERALL : INCIDENT  ($incident non-mock row(s) in trades.jsonl; see docs\phase5f_scheduler_runbook.md section 6)"
    exit 1
} elseif ($degraded -gt 0) {
    Write-Host "OVERALL : DEGRADED  ($degraded check(s) WARN/STALE/OFF/DEAD/ABSENT)"
    exit 2
} else {
    Write-Host "OVERALL : OK"
    exit 0
}
