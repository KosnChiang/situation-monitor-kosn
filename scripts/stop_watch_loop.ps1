# ============================================================
# scripts/stop_watch_loop.ps1 -- gentle stop for the Phase-5.D
# watch loop and the Phase-5.5 mock quote feed.
#
# Reads the PID files written by:
#   * scripts/watch_loop_run.ps1  -> logs/watch_loop.pid.log
#   * scripts/quote_feed_run.ps1  -> logs/quote_feed.pid.log
# and calls Stop-Process on each PID, then removes the PID file.
#
# If a PID file is absent the script falls back to scanning for
# python processes whose CommandLine matches the loop / feed module
# names; in fallback mode it ONLY REPORTS (does not kill) so an
# operator can decide what to do.
#
# This script does NOT pin the mock envelope -- it is a shutdown
# command, not a runtime, and the python processes it targets each
# enforced the envelope when they started. Operators can run it
# from any PowerShell session without a prior setup script.
#
# Examples:
#   .\scripts\stop_watch_loop.ps1                 # stop both
#   .\scripts\stop_watch_loop.ps1 -OnlyLoop       # stop watch loop only
#   .\scripts\stop_watch_loop.ps1 -OnlyFeed       # stop quote feed only
# ============================================================

param(
    [switch]$OnlyLoop,
    [switch]$OnlyFeed
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

if ($OnlyLoop -and $OnlyFeed) {
    Write-Host "-OnlyLoop and -OnlyFeed both set: nothing to do."
    exit 0
}

$targets = [ordered]@{}
if (-not $OnlyFeed) { $targets["watch_loop"] = "logs\watch_loop.pid.log" }
if (-not $OnlyLoop) { $targets["quote_feed"] = "logs\quote_feed.pid.log" }

$stoppedCount = 0
$missingCount = 0

foreach ($name in $targets.Keys) {
    $pidFile = $targets[$name]
    if (-not (Test-Path $pidFile)) {
        Write-Host "[$name] no PID file at $pidFile -- falling back to cmdline scan"
        $needle = if ($name -eq "watch_loop") { "watch_fibo_loop" } else { "tools\.quote_feed" }
        try {
            $matches = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -and ($_.CommandLine -match $needle) }
        } catch {
            $matches = $null
        }
        if ($matches) {
            foreach ($p in $matches) {
                Write-Host "  candidate pid=$($p.ProcessId) cmd=$($p.CommandLine)"
            }
            Write-Host "  (fallback mode -- not killing; re-run after writing $pidFile, or use Stop-Process manually)"
        } else {
            Write-Host "  no matching python process found; $name already stopped"
        }
        $missingCount++
        continue
    }

    $pidValue = (Get-Content $pidFile -Raw).Trim()
    $pidInt = 0
    if (-not [int]::TryParse($pidValue, [ref]$pidInt)) {
        Write-Host "[$name] PID file $pidFile contains non-integer '$pidValue'; removing it"
        Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        continue
    }

    $proc = Get-Process -Id $pidInt -ErrorAction SilentlyContinue
    if ($proc) {
        try {
            Stop-Process -Id $pidInt -Force
            Write-Host "[$name] stopped pid=$pidInt"
            $stoppedCount++
        } catch {
            Write-Host "[$name] Stop-Process failed for pid=${pidInt}: $($_.Exception.Message)"
        }
    } else {
        Write-Host "[$name] pid=$pidInt already gone"
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "----- stop summary -----"
Write-Host "  stopped         : $stoppedCount"
Write-Host "  missing PID file: $missingCount (used fallback scan; check output above)"
exit 0
