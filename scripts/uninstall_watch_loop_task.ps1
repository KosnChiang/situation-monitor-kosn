# ============================================================
# scripts/uninstall_watch_loop_task.ps1 -- remove the Phase-5.F
# scheduled tasks (watch_loop + quote_feed) and clean up.
#
# Idempotent: if a task is missing, no error. If a task is Running,
# stops it before unregistering.
#
# Does NOT pin the mock envelope -- this is a teardown command, not
# a runtime, so it can be run from any PowerShell session. The tasks
# being unregistered each ran under the mock envelope when alive.
#
# Examples:
#   .\scripts\uninstall_watch_loop_task.ps1               # remove both
#   .\scripts\uninstall_watch_loop_task.ps1 -KeepPidFiles # leave .pid.log behind
# ============================================================

param(
    [switch]$KeepPidFiles
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

$taskFolder = "\AIFiboVisionTrader\"
$names = @("watch_loop", "quote_feed")

$removed = 0
$missing = 0

foreach ($name in $names) {
    $task = Get-ScheduledTask -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "[uninstall] ${taskFolder}${name}: not present"
        $missing++
        continue
    }

    # Stop first if Running, so Unregister doesn't fight a live job.
    $info = Get-ScheduledTaskInfo -TaskPath $taskFolder -TaskName $name -ErrorAction SilentlyContinue
    if ($task.State -eq "Running") {
        Write-Host "[uninstall] ${taskFolder}${name}: stopping (was Running)"
        try {
            Stop-ScheduledTask -TaskPath $taskFolder -TaskName $name
        } catch {
            Write-Host "[uninstall]   Stop-ScheduledTask failed: $($_.Exception.Message) (continuing)"
        }
    }

    Unregister-ScheduledTask -TaskPath $taskFolder -TaskName $name -Confirm:$false
    Write-Host "[uninstall] ${taskFolder}${name}: unregistered"
    $removed++
}

# Try to remove the now-empty task folder. The *-ScheduledTask cmdlets
# don't expose folder removal, so use the COM Scheduler.Service.
try {
    $svc = New-Object -ComObject Schedule.Service
    $svc.Connect()
    $root = $svc.GetFolder("\")
    # Only delete if the folder exists and is empty.
    try {
        $folder = $root.GetFolder($taskFolder.Trim('\'))
        $childTasks = @($folder.GetTasks(0))
        $childFolders = @($folder.GetFolders(0))
        if ($childTasks.Count -eq 0 -and $childFolders.Count -eq 0) {
            $root.DeleteFolder($taskFolder.Trim('\'), 0)
            Write-Host "[uninstall] removed empty task folder $taskFolder"
        } else {
            Write-Host "[uninstall] $taskFolder still has $($childTasks.Count) task(s) + $($childFolders.Count) folder(s); leaving it"
        }
    } catch {
        # Folder might not exist (never installed) -- not an error.
    }
} catch {
    Write-Host "[uninstall] COM Scheduler.Service unavailable; task folder cleanup skipped: $($_.Exception.Message)"
}

# Optionally clean PID files left behind by 5.E wrappers.
if (-not $KeepPidFiles) {
    foreach ($pidFile in @("logs\watch_loop.pid.log", "logs\quote_feed.pid.log")) {
        if (Test-Path $pidFile) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
            Write-Host "[uninstall] removed $pidFile"
        }
    }
}

Write-Host ""
Write-Host "----- uninstall summary -----"
Write-Host "  removed   : $removed"
Write-Host "  missing   : $missing (no-op)"
Write-Host "  pid files : $(if ($KeepPidFiles) { 'kept (-KeepPidFiles)' } else { 'cleaned' })"
exit 0
