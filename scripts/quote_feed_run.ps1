# ============================================================
# scripts/quote_feed_run.ps1 -- foreground mock quote feed.
#
# Companion to scripts/watch_loop_run.ps1. The Phase-5.5 design
# (docs/quote_feed_design.md) requires the quote side and the
# execution side to run in different processes; this wrapper is
# the operator entry for the quote side.
#
# Safety properties:
#   * Refuses to run on a hot envelope.
#   * Refuses to run if any well-known broker credential env is set.
#   * Provider is hard-coded to 'mock' (the only PROVIDERS entry
#     today). No network calls, no credential reads.
#   * tools/quote_feed.py::_refuse_if_live() re-checks at startup.
#   * Writes PID to logs/quote_feed.pid.log for stop_watch_loop.ps1.
#
# Output:
#   Appends one JSONL row per fetch to --Output (default
#   logs/quotes.jsonl). The companion watch loop tails the last
#   line; do NOT truncate this file while the loop is running.
#
# Example:
#   .\scripts\quote_feed_run.ps1                                # XAUUSD @ $2250, 1s, infinite
#   .\scripts\quote_feed_run.ps1 -Symbol XAUUSD -BasePrice 2400 -Interval 2 -MaxIterations 30
#   .\scripts\quote_feed_run.ps1 -Seed 42 -BasePrice 2250.0     # reproducible
# ============================================================

param(
    [string]$Symbol = "XAUUSD",
    [double]$BasePrice = 2250.0,
    [double]$Interval = 1.0,
    [int]$MaxIterations = 0,
    [int]$Seed = 0,
    [string]$Output = "logs\quotes.jsonl"
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

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

# ----- 4. Resolve venv python ---------------------------------------------
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

# ----- 5. PID file (for stop_watch_loop.ps1) -------------------------------
$pidFile = "logs\quote_feed.pid.log"
$PID | Out-File -FilePath $pidFile -Encoding ascii -Force

# ----- 6. Echo + run + cleanup --------------------------------------------
Write-Host "quote_feed_run.ps1 -- mock-only quote feed (foreground)"
Write-Host "  projectRoot    : $projectRoot"
Write-Host "  python         : $python"
Write-Host "  provider       : mock (hard-coded)"
Write-Host "  symbol         : $Symbol"
Write-Host "  base_price     : $BasePrice"
Write-Host "  interval       : $Interval s"
Write-Host "  max_iterations : $(if ($MaxIterations -eq 0) { 'infinite (Ctrl+C to stop)' } else { $MaxIterations })"
Write-Host "  seed           : $Seed"
Write-Host "  output         : $Output"
Write-Host "  pid file       : $pidFile (PID=$PID)"
Write-Host ""

try {
    & $python -m tools.quote_feed `
        --provider mock `
        --symbol $Symbol `
        --base-price $BasePrice `
        --interval $Interval `
        --max-iterations $MaxIterations `
        --seed $Seed `
        --output $Output
    $feedExit = $LASTEXITCODE
} finally {
    if (Test-Path $pidFile) { Remove-Item $pidFile -Force -ErrorAction SilentlyContinue }
}

exit $feedExit
