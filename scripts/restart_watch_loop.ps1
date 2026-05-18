# ============================================================
# scripts/restart_watch_loop.ps1 -- stop + start the Phase-5.F
# scheduled tasks. For tasks registered by
# scripts/install_watch_loop_task.ps1.
#
# Calls Stop-ScheduledTask, polls Get-ScheduledTaskInfo until the
# task's State stops being Running (max -TimeoutSec, default 10),
# then calls Start-ScheduledTask. Reports per-task outcome.
#
# Does NOT pin the mock envelope -- the underlying task action
# already pins LIVE_TRADING=false / EXECUTION_MODE=mock /
# BROKER_MODE=mock at runtime (see install_watch_loop_task.ps1).
#
# For non-task (foreground) restart, use:
#   .\scripts\stop_watch_loop.ps1
#   .\scripts\watch_loop_run.ps1
# (Phase 5.E PID-file workflow.)
#
# Examples:
#   .\scripts\restart_watch_loop.ps1                 # restart both
#   .\scripts\restart_watch_loop.ps1 -OnlyLoop       # restart only watch_loop
#   .\scripts\restart_watch_loop.ps1 -OnlyFeed       # restart only quote_feed
#   .\scripts\restart_watch_loop.ps1 -TimeoutSec 20  # longer stop wait
# ============================================================

param(
    [switch]$OnlyLoop,
    [switch]$OnlyFeed,
    [int]$TimeoutSec = 10
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

if ($OnlyLoop -and $OnlyFeed) {
    Write-Host "-OnlyLoop and -OnlyFeed both set: nothing to do."
    exit 0
}

$taskFolder = "\AIFiboVisionTrader\"
$names = @()
if (-not $OnlyFeed) { $names += "watch_loop" }
if (-not $OnlyLoop) { $names += "quote_feed" }

$restarted = 0
$skipped = 0

foreach ($name in $names) {
    $task = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "[restart] ${taskFolder}${name}: not registered -- run install_watch_loop_task.ps1 first"
        $skipped++
        continue
    }
    if ($task.State -eq "Disabled") {
        Write-Host "[restart] ${taskFolder}${name}: Disabled -- enable with Enable-ScheduledTask before restarting"
        $skipped++
        continue
    }

    # Stop if Running; poll until not Running (max $TimeoutSec).
    if ($task.State -eq "Running") {
        Write-Host "[restart] ${taskFolder}${name}: stopping (was Running)"
        try {
            Stop-ScheduledTask -TaskPath $taskFolder -TaskName $name
        } catch {
            Write-Host "[restart]   Stop-ScheduledTask raised: $($_.Exception.Message) (continuing to poll)"
        }
        $deadline = (Get-Date).AddSeconds($TimeoutSec)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            $cur = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
            if ($cur.State -ne "Running") { break }
        }
        $cur = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
        if ($cur.State -eq "Running") {
            Write-Host "[restart]   WARNING: still Running after ${TimeoutSec}s; starting anyway (MultipleInstances=IgnoreNew will protect)"
        } else {
            Write-Host "[restart]   stopped (state=$($cur.State))"
        }
    } else {
        Write-Host "[restart] ${taskFolder}${name}: state was $($task.State) (not Running) -- starting"
    }

    Start-ScheduledTask -TaskPath $taskFolder -TaskName $name
    Write-Host "[restart] ${taskFolder}${name}: started"
    $restarted++
}

Write-Host ""
Write-Host "----- restart summary -----"
Write-Host "  restarted : $restarted"
Write-Host "  skipped   : $skipped"
Write-Host ""
Write-Host "Verify with: .\scripts\watch_loop_status.ps1"
exit 0
