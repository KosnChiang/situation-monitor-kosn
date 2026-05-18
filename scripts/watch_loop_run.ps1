# ============================================================
# scripts/watch_loop_run.ps1 -- foreground operator entry for the
# Fibo CV watch loop. Mock-only by design.
#
# Companion to scripts/quote_feed_run.ps1 -- the operator runs the
# quote feed in Terminal A and this loop in Terminal B. The loop
# tails logs/quotes.jsonl (or whatever --quotes-file points at),
# converts the latest price to pixel-y via the calibration block in
# --config, and writes one structured row per iteration to
# logs/watch_loop.jsonl.
#
# Safety properties:
#   * Refuses to run if LIVE_TRADING / EXECUTION_MODE / BROKER_MODE
#     are already set to anything but the mock-only values.
#   * Refuses to run if any well-known broker credential env is set.
#   * Pins LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock
#     for the python process it spawns. The python layer
#     (tools/watch_fibo_loop.py::_refuse_if_live) re-checks.
#   * --Submit is OPT-IN. Without it the loop never calls RiskGate
#     or MockExecutor; logs/trades.jsonl is untouched.
#   * --LiveCapture is OPT-IN. Without it the loop reads --CapturePng
#     each iteration, so mss is never imported and no display is required.
#   * Writes its own PID to logs/watch_loop.pid.log so the matching
#     stop_watch_loop.ps1 can find and terminate it cleanly. The
#     .pid.log extension lands under the existing logs/*.log gitignore
#     entry; no .gitignore modification is required.
#
# Example:
#   .\scripts\watch_loop_run.ps1                          # dry, infinite, offline
#   .\scripts\watch_loop_run.ps1 -MaxIterations 10 -Interval 2
#   .\scripts\watch_loop_run.ps1 -Submit                  # opt-in mock fills
#   .\scripts\watch_loop_run.ps1 -LiveCapture -Submit     # real mss + mock fills
# ============================================================

param(
    [int]$Interval = 5,
    [int]$MaxIterations = 0,
    [string]$Config = "config\capture.yaml",
    [string]$CapturePng = "logs\capture_test.png",
    [int]$TolerancePx = 8,
    [int]$DedupeCooldown = 60,
    [string]$QuotesFile = "logs\quotes.jsonl",
    [string]$WatchLog = "logs\watch_loop.jsonl",
    [string]$Symbol = "MOCK",
    [switch]$Submit,
    [switch]$LiveCapture,
    [switch]$DedupeFromTrades,
    [string]$NotifyMode = "off"
)

$ErrorActionPreference = "Stop"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

# ----- 1. Refuse if envelope is hot ----------------------------------------
if ($env:LIVE_TRADING -and $env:LIVE_TRADING -ne "false") {
    Write-Error "Refusing to run: LIVE_TRADING=$($env:LIVE_TRADING). Must be 'false'."
}
if ($env:EXECUTION_MODE -and $env:EXECUTION_MODE -ne "mock") {
    Write-Error "Refusing to run: EXECUTION_MODE=$($env:EXECUTION_MODE). Must be 'mock'."
}
if ($env:BROKER_MODE -and $env:BROKER_MODE -ne "mock") {
    Write-Error "Refusing to run: BROKER_MODE=$($env:BROKER_MODE). Must be 'mock'."
}

# ----- 2. Refuse if any broker credential env is set -----------------------
$Forbidden = @(
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
    "IB_ACCOUNT", "IB_USERNAME", "IB_PASSWORD",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN"
)
foreach ($name in $Forbidden) {
    if ([System.Environment]::GetEnvironmentVariable($name, "Process")) {
        Write-Error "Refusing to run: forbidden broker credential env '$name' is set."
    }
}

# ----- 3. Pin mock envelope ------------------------------------------------
$env:LIVE_TRADING        = "false"
$env:EXECUTION_MODE      = "mock"
$env:BROKER_MODE         = "mock"
$env:CUDA_VISIBLE_DEVICES = "1"
$env:PYTHONPATH          = $projectRoot
$env:PYTHONUTF8          = "1"

# ----- 4. Verify required artifacts ----------------------------------------
if (-not $LiveCapture -and -not (Test-Path $CapturePng)) {
    Write-Error "Capture PNG not found: $CapturePng`nHint: pass -LiveCapture or run ``python -m tools.capture_test`` first."
}

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

# ----- 5. Resolve venv python ---------------------------------------------
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

# ----- 6. PID file (for stop_watch_loop.ps1) -------------------------------
$pidFile = "logs\watch_loop.pid.log"
$PID | Out-File -FilePath $pidFile -Encoding ascii -Force

# ----- 7. Build CLI args ---------------------------------------------------
$pyArgs = @(
    "-m", "tools.watch_fibo_loop",
    "--config", $Config,
    "--quotes-file", $QuotesFile,
    "--watch-log",   $WatchLog,
    "--interval", $Interval,
    "--max-iterations", $MaxIterations,
    "--tolerance-px", $TolerancePx,
    "--dedupe-cooldown-sec", $DedupeCooldown,
    "--notify-mode", $NotifyMode,
    "--symbol", $Symbol
)
if ($LiveCapture) {
    $pyArgs += "--live-capture"
} else {
    $pyArgs += @("--offline-image", $CapturePng)
}
if ($Submit) { $pyArgs += "--submit" }
if ($DedupeFromTrades) { $pyArgs += "--dedupe-from-trades-log" }

# ----- 8. Echo + run + cleanup --------------------------------------------
Write-Host "watch_loop_run.ps1 -- mock-only loop (foreground)"
Write-Host "  projectRoot    : $projectRoot"
Write-Host "  python         : $python"
Write-Host "  capture mode   : $(if ($LiveCapture) { 'live (mss)' } else { "offline ($CapturePng)" })"
Write-Host "  config         : $Config"
Write-Host "  quotes_file    : $QuotesFile"
Write-Host "  watch_log      : $WatchLog"
Write-Host "  interval       : $Interval s"
Write-Host "  max_iterations : $(if ($MaxIterations -eq 0) { 'infinite (Ctrl+C to stop)' } else { $MaxIterations })"
Write-Host "  submit         : $($Submit.IsPresent)"
Write-Host "  notify_mode    : $NotifyMode"
Write-Host "  pid file       : $pidFile (PID=$PID)"
Write-Host ""

try {
    & $python @pyArgs
    $loopExit = $LASTEXITCODE
} finally {
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
}

exit $loopExit
