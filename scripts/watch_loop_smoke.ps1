# ============================================================
# scripts/watch_loop_smoke.ps1 -- one-shot mock-only dry-run smoke for
# the Fibo CV watch loop.
#
# What it does:
#   1. Pins LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock.
#      Refuses to run if any of those are already set to anything else.
#   2. Refuses to run if any well-known broker credential env is set.
#   3. Writes ONE synthetic quote (default last=$2250.0) to
#      logs/quotes_smoke.jsonl so the loop has something to read; this
#      keeps the smoke self-contained (no need to spawn quote_feed).
#   4. Isolates trades.jsonl via $env:TRADES_LOG -> logs/trades_smoke.jsonl
#      so the production logs/trades.jsonl is never touched.
#   5. Runs tools.watch_fibo_loop --offline-image logs/capture_test.png
#      with the Phase-5.C demo calibration for N iterations.
#   6. --Submit is OPT-IN: omit it and the loop is a pure dry-run with
#      no RiskGate, no MockExecutor, no trades.jsonl write at all.
#   7. Prints a short summary (rows written, side / fibo_y / dedupe per
#      iteration) and exits with the loop's exit code.
#
# Required artifact:
#   logs/capture_test.png  (run python -m tools.capture_test to refresh)
#
# Example:
#   .\scripts\watch_loop_smoke.ps1                       # dry, 3 iters
#   .\scripts\watch_loop_smoke.ps1 -Iterations 5
#   .\scripts\watch_loop_smoke.ps1 -Submit               # opt-in mock fill
# ============================================================

param(
    [int]$Iterations = 3,
    [string]$Calibration = "tests\fixtures\capture_calibration_demo.yaml",
    [double]$QuotePrice = 2250.0,
    [string]$CapturePng = "logs\capture_test.png",
    [switch]$Submit
)

$ErrorActionPreference = "Stop"

# Resolve repo root regardless of cwd.
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
if (-not (Test-Path $CapturePng)) {
    Write-Error "Capture PNG not found: $CapturePng`nHint: run ``python -m tools.capture_test`` first."
}
if (-not (Test-Path $Calibration)) {
    Write-Error "Calibration config not found: $Calibration"
}

# ----- 5. Prepare isolated I/O paths --------------------------------------
$quotesFile = "logs\quotes_smoke.jsonl"
$watchLog   = "logs\watch_loop_smoke.jsonl"
$tradesLog  = "logs\trades_smoke.jsonl"

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

# Wipe previous smoke outputs so two consecutive runs produce identical state.
if (Test-Path $watchLog)  { Remove-Item $watchLog  -Force }
if (Test-Path $tradesLog) { Remove-Item $tradesLog -Force }

# Redirect MockExecutor's writes into the isolated smoke trades log so the
# production logs/trades.jsonl is never touched even with -Submit.
$env:TRADES_LOG = $tradesLog

# Write one synthetic quote that maps via the demo calibration to pixel_y=1533
# (the filtered Fibo line in logs/fibo_lines_filtered.json from Phase 5.B).
$quote = [pscustomobject]@{
    symbol    = "XAUUSD"
    bid       = $QuotePrice - 0.25
    ask       = $QuotePrice + 0.25
    last      = $QuotePrice
    mid       = $QuotePrice
    ts        = 1.0
    timestamp = "2026-05-18T00:00:00+00:00"
    source    = "mock"
} | ConvertTo-Json -Compress
Set-Content -Path $quotesFile -Value $quote -Encoding utf8

# ----- 6. Resolve venv python ---------------------------------------------
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

# ----- 7. Echo + run -------------------------------------------------------
Write-Host "watch_loop_smoke.ps1 -- mock-only dry-run smoke"
Write-Host "  projectRoot    : $projectRoot"
Write-Host "  python         : $python"
Write-Host "  capture        : $CapturePng"
Write-Host "  calibration    : $Calibration"
Write-Host "  quote (synth)  : last=$QuotePrice -> $quotesFile"
Write-Host "  watch_log      : $watchLog"
Write-Host "  trades_log     : $tradesLog (TRADES_LOG isolated)"
Write-Host "  iterations     : $Iterations"
Write-Host "  submit         : $($Submit.IsPresent)"
Write-Host ""

$pyArgs = @(
    "-m", "tools.watch_fibo_loop",
    "--offline-image", $CapturePng,
    "--config", $Calibration,
    "--quotes-file", $quotesFile,
    "--watch-log",   $watchLog,
    "--max-iterations", $Iterations,
    "--interval", "0",
    "--dedupe-cooldown-sec", "300",
    "--tolerance-px", "8",
    "--symbol", "XAUUSD"
)
if ($Submit) { $pyArgs += "--submit" }

& $python @pyArgs
$loopExit = $LASTEXITCODE

# ----- 8. Summary ----------------------------------------------------------
Write-Host ""
Write-Host "----- smoke summary -----"
if (Test-Path $watchLog) {
    $rows = Get-Content $watchLog
    Write-Host "$watchLog -- $($rows.Count) row(s)"
    foreach ($line in $rows) {
        $r = $line | ConvertFrom-Json
        $sub = if ($r.execution) { $r.execution.submitted } else { $false }
        "{0,3}  side={1,-5} y={2,5}  conf={3:N2}  dedupe={4,-24} submitted={5}" -f `
            $r.iter, $r.signal.side, $r.signal.fibo_line_y, $r.signal.confidence, `
            $r.dedupe.action, $sub
    }
} else {
    Write-Host "$watchLog -- NOT created (loop exited before writing any row)"
}

if (Test-Path $tradesLog) {
    $fillCount = (Get-Content $tradesLog | Measure-Object -Line).Lines
    Write-Host "$tradesLog -- $fillCount mock fill(s)"
} else {
    Write-Host "$tradesLog -- not created (expected when -Submit is omitted)"
}

exit $loopExit
