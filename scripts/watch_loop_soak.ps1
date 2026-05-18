# ============================================================
# scripts/watch_loop_soak.ps1 -- mock-only 30-60 min soak test.
#
# What it does:
#   1. Pins LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock,
#      refuses to run on a hot envelope; refuses on broker creds.
#   2. Wipes prior soak artifacts (NEVER touches the production logs
#      logs/quotes.jsonl / logs/watch_loop.jsonl / logs/trades.jsonl).
#      All soak I/O lands on isolated *_soak filenames.
#   3. Starts mock quote feed (Start-Job, max-iter so it self-stops).
#   4. Sleeps 2s so the loop's first iteration has a quote to read.
#   5. Starts watch_fibo_loop (Start-Job, max-iter so it self-stops).
#   6. Every -SnapshotIntervalSec seconds writes a resource snapshot
#      (PIDs, working set, CPU time, GPU memory/util via nvidia-smi,
#      watch_loop.jsonl row count + size) to logs/soak_snapshots.jsonl.
#      Drains the job buffers into logs/soak_jobs.log so the analyzer
#      can grep for Traceback / Refusing / LiveTradingForbidden.
#   7. After both jobs finish, runs tools.analyze_soak which writes
#      logs/soak_report_<timestamp>.log and .json siblings. Exit code
#      is the analyzer's exit code: 0 PASS, 1 FAIL, 2 DEGRADED.
#
# -Submit is OPT-IN. Without it, no MockExecutor call, no trades
# anywhere. With it, MockExecutor writes to logs/trades_soak.jsonl
# (TRADES_LOG env is redirected); the production logs/trades.jsonl is
# never touched even with -Submit.
#
# Required artifacts:
#   logs/capture_test.png
#   tests/fixtures/capture_calibration_demo.yaml
#
# Example:
#   .\scripts\watch_loop_soak.ps1                           # 30 min dry
#   .\scripts\watch_loop_soak.ps1 -DurationMin 5            # 5 min mini-soak
#   .\scripts\watch_loop_soak.ps1 -DurationMin 30 -Submit   # opt-in mock fills
# ============================================================

param(
    [int]$DurationMin = 30,
    [int]$LoopInterval = 5,
    [double]$QuoteInterval = 1.0,
    [double]$BasePrice = 2250.0,
    [string]$Symbol = "XAUUSD",
    [string]$Calibration = "tests\fixtures\capture_calibration_demo.yaml",
    [string]$CapturePng = "logs\capture_test.png",
    [int]$SnapshotIntervalSec = 60,
    [int]$DedupeCooldown = 60,
    [switch]$Submit
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
if (-not (Test-Path $CapturePng)) {
    Write-Error "Capture PNG not found: $CapturePng`nHint: run ``python -m tools.capture_test`` first."
}
if (-not (Test-Path $Calibration)) {
    Write-Error "Calibration config not found: $Calibration"
}

if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

# ----- 5. Isolated soak paths (NEVER touch production logs) ----------------
$soakQuotes   = "logs\quotes_soak.jsonl"
$soakWatch    = "logs\watch_loop_soak.jsonl"
$soakTrades   = "logs\trades_soak.jsonl"
$soakSnaps    = "logs\soak_snapshots.jsonl"
$soakJobsLog  = "logs\soak_jobs.log"
$stamp        = Get-Date -Format "yyyyMMdd-HHmmss"
$soakReport   = "logs\soak_report_$stamp.log"

# Wipe previous soak outputs (these are dedicated to the soak; never
# the production logs).
foreach ($p in @($soakQuotes, $soakWatch, $soakTrades, $soakSnaps, $soakJobsLog)) {
    if (Test-Path $p) { Remove-Item $p -Force }
}

# Redirect MockExecutor's writes into the isolated soak trades log so the
# real logs/trades.jsonl is never touched, even with -Submit.
$env:TRADES_LOG = $soakTrades

# ----- 6. Resolve venv python ---------------------------------------------
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

# ----- 7. Compute auto-stop iteration counts -------------------------------
$quoteMax = [int]([math]::Ceiling($DurationMin * 60.0 / $QuoteInterval))
$loopMax  = [int]([math]::Ceiling($DurationMin * 60.0 / $LoopInterval))

# ----- 8. Echo --------------------------------------------------------------
Write-Host "watch_loop_soak.ps1 -- mock-only $DurationMin min soak"
Write-Host "  projectRoot    : $projectRoot"
Write-Host "  python         : $python"
Write-Host "  duration       : $DurationMin min"
Write-Host "  loop_interval  : $LoopInterval s   (max-iterations $loopMax)"
Write-Host "  quote_interval : $QuoteInterval s (max-iterations $quoteMax)"
Write-Host "  capture        : $CapturePng"
Write-Host "  calibration    : $Calibration"
Write-Host "  base_price     : $BasePrice"
Write-Host "  snapshot every : $SnapshotIntervalSec s"
Write-Host "  submit         : $($Submit.IsPresent)"
Write-Host "  isolated paths : $soakQuotes / $soakWatch / $soakTrades / $soakSnaps"
Write-Host "  report         : $soakReport"
Write-Host ""

# ----- 9. Start quote feed job --------------------------------------------
$feedJob = Start-Job -Name 'soak_quote_feed' -ScriptBlock {
    param($repoRoot, $pythonExe, $symbol, $basePrice, $interval, $maxIter, $output)
    Set-Location $repoRoot
    $env:LIVE_TRADING   = "false"
    $env:EXECUTION_MODE = "mock"
    $env:BROKER_MODE    = "mock"
    $env:PYTHONPATH     = $repoRoot
    $env:PYTHONUTF8     = "1"
    & $pythonExe -m tools.quote_feed `
        --provider mock `
        --symbol $symbol `
        --base-price $basePrice `
        --interval $interval `
        --max-iterations $maxIter `
        --output $output 2>&1
} -ArgumentList $projectRoot, $python, $Symbol, $BasePrice, $QuoteInterval, $quoteMax, $soakQuotes

Write-Host "[soak] started quote_feed job id=$($feedJob.Id), max-iter=$quoteMax"

# Give the feed a moment to write its first record.
Start-Sleep -Seconds 2

# ----- 10. Start watch loop job -------------------------------------------
$loopJob = Start-Job -Name 'soak_watch_loop' -ScriptBlock {
    param($repoRoot, $pythonExe, $capturePng, $calibration, $quotesFile,
          $watchLog, $loopInterval, $maxIter, $tolerancePx, $dedupeCooldown,
          $symbol, $submit, $tradesLog)
    Set-Location $repoRoot
    $env:LIVE_TRADING   = "false"
    $env:EXECUTION_MODE = "mock"
    $env:BROKER_MODE    = "mock"
    $env:CUDA_VISIBLE_DEVICES = "1"
    $env:PYTHONPATH     = $repoRoot
    $env:PYTHONUTF8     = "1"
    $env:TRADES_LOG     = $tradesLog
    $pyArgs = @(
        "-m", "tools.watch_fibo_loop",
        "--offline-image", $capturePng,
        "--config", $calibration,
        "--quotes-file", $quotesFile,
        "--watch-log",   $watchLog,
        "--interval", $loopInterval,
        "--max-iterations", $maxIter,
        "--tolerance-px", $tolerancePx,
        "--dedupe-cooldown-sec", $dedupeCooldown,
        "--symbol", $symbol
    )
    if ($submit) { $pyArgs += "--submit" }
    & $pythonExe @pyArgs 2>&1
} -ArgumentList `
    $projectRoot, $python, $CapturePng, $Calibration, $soakQuotes,
    $soakWatch, $LoopInterval, $loopMax, 8, $DedupeCooldown,
    $Symbol, $Submit.IsPresent, $soakTrades

Write-Host "[soak] started watch_loop job id=$($loopJob.Id), max-iter=$loopMax"
Write-Host ""

# ----- 11. Snapshot + drain loop ------------------------------------------
function Find-SoakPid {
    param([string]$Needle)
    try {
        $procs = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                   Where-Object { $_.CommandLine -and ($_.CommandLine -match $Needle) })
        if ($procs.Count -gt 0) { return [int]$procs[0].ProcessId }
    } catch {}
    return $null
}

$snapshotIdx = 0
$startTs = (Get-Date)
$jobDone = { ($loopJob.State -in @('Completed','Failed','Stopped')) -and `
             ($feedJob.State -in @('Completed','Failed','Stopped')) }

while (-not (& $jobDone)) {
    Start-Sleep -Seconds $SnapshotIntervalSec

    $snapshotIdx++
    $nowTs = [double](Get-Date -UFormat %s)

    # Discover PIDs by command-line match (Start-Job's $PID is the
    # PowerShell wrapper, not python).
    $loopPid = Find-SoakPid 'watch_fibo_loop'
    $feedPid = Find-SoakPid 'tools\.quote_feed'

    $loopProc = if ($loopPid) { Get-Process -Id $loopPid -ErrorAction SilentlyContinue } else { $null }
    $feedProc = if ($feedPid) { Get-Process -Id $feedPid -ErrorAction SilentlyContinue } else { $null }

    # nvidia-smi best-effort
    $gpu0Mem = $null; $gpu0Util = $null; $gpu1Mem = $null; $gpu1Util = $null
    try {
        $smi = & nvidia-smi --query-gpu=memory.used,utilization.gpu --format=csv,noheader,nounits 2>$null
        if ($LASTEXITCODE -eq 0 -and $smi) {
            $lines = @($smi | Where-Object { $_ -and ($_.Trim()) })
            if ($lines.Count -ge 1) {
                $p = $lines[0] -split ',' | ForEach-Object { $_.Trim() }
                $gpu0Mem = [int]$p[0]; $gpu0Util = [int]$p[1]
            }
            if ($lines.Count -ge 2) {
                $p = $lines[1] -split ',' | ForEach-Object { $_.Trim() }
                $gpu1Mem = [int]$p[0]; $gpu1Util = [int]$p[1]
            }
        }
    } catch {}

    # File metrics
    $wlRows = 0; $wlSizeKb = 0
    if (Test-Path $soakWatch) {
        $wlRows = @(Get-Content $soakWatch).Count
        $wlSizeKb = [int]((Get-Item $soakWatch).Length / 1024)
    }

    $snap = [pscustomobject]@{
        ts                     = $nowTs
        timestamp              = (Get-Date -Format o)
        snapshot_idx           = $snapshotIdx
        watch_loop_pid         = $loopPid
        quote_feed_pid         = $feedPid
        watch_loop_ws_mb       = if ($loopProc) { [int]($loopProc.WorkingSet64 / 1MB) } else { $null }
        quote_feed_ws_mb       = if ($feedProc) { [int]($feedProc.WorkingSet64 / 1MB) } else { $null }
        watch_loop_cpu_sec     = if ($loopProc) { [double]$loopProc.CPU } else { $null }
        quote_feed_cpu_sec     = if ($feedProc) { [double]$feedProc.CPU } else { $null }
        gpu0_mem_mb            = $gpu0Mem
        gpu0_util_pct          = $gpu0Util
        gpu1_mem_mb            = $gpu1Mem
        gpu1_util_pct          = $gpu1Util
        watch_loop_jsonl_rows  = $wlRows
        watch_loop_jsonl_size_kb = $wlSizeKb
    } | ConvertTo-Json -Compress
    Add-Content -Path $soakSnaps -Value $snap -Encoding utf8

    # Drain job buffers so they don't grow unbounded.
    if ($loopJob.HasMoreData) {
        $out = Receive-Job -Job $loopJob -Keep:$false 2>&1
        if ($out) { ($out | Out-String).TrimEnd() | Add-Content -Path $soakJobsLog -Encoding utf8 }
    }
    if ($feedJob.HasMoreData) {
        $out = Receive-Job -Job $feedJob -Keep:$false 2>&1
        if ($out) { ($out | Out-String).TrimEnd() | Add-Content -Path $soakJobsLog -Encoding utf8 }
    }

    $elapsed = [int]((Get-Date) - $startTs).TotalSeconds
    Write-Host "[soak] snap #$snapshotIdx t+${elapsed}s loop_pid=$loopPid feed_pid=$feedPid wl_rows=$wlRows wl_size=${wlSizeKb}KB"
}

# ----- 12. Final drain + cleanup ------------------------------------------
Write-Host ""
Write-Host "[soak] both jobs done (loop=$($loopJob.State) feed=$($feedJob.State)); finalizing..."

$loopFinal = Receive-Job -Job $loopJob -Wait 2>&1
if ($loopFinal) { ($loopFinal | Out-String).TrimEnd() | Add-Content -Path $soakJobsLog -Encoding utf8 }
$feedFinal = Receive-Job -Job $feedJob -Wait 2>&1
if ($feedFinal) { ($feedFinal | Out-String).TrimEnd() | Add-Content -Path $soakJobsLog -Encoding utf8 }

$jobsOk = ($loopJob.State -eq 'Completed') -and ($feedJob.State -eq 'Completed')
Remove-Job -Job $loopJob -Force
Remove-Job -Job $feedJob -Force

# ----- 13. Run analyzer ---------------------------------------------------
Write-Host ""
Write-Host "[soak] running tools.analyze_soak..."

$analyzeArgs = @(
    "-m", "tools.analyze_soak",
    "--duration-min", $DurationMin,
    "--loop-interval", $LoopInterval,
    "--quote-interval", $QuoteInterval,
    "--watch-log",  $soakWatch,
    "--quotes-log", $soakQuotes,
    "--snapshots",  $soakSnaps,
    "--captured-log", $soakJobsLog,
    "--out",        $soakReport
)
if ($Submit) { $analyzeArgs += @("--submit", "--trades-log", $soakTrades) }
if ($jobsOk) { $analyzeArgs += "--jobs-ok" }

& $python @analyzeArgs
$analyzerExit = $LASTEXITCODE

Write-Host ""
Write-Host "----- soak report saved -----"
Write-Host "  text  : $soakReport"
Write-Host "  json  : $($soakReport -replace '\.log$','.json')"
Write-Host ""
Write-Host "----- production logs sanity (must be UNCHANGED by this soak) -----"
foreach ($p in @("logs\quotes.jsonl", "logs\watch_loop.jsonl", "logs\trades.jsonl")) {
    if (Test-Path $p) {
        $info = Get-Item $p
        Write-Host ("  {0,-28} size={1} bytes lastWrite={2}" -f $p, $info.Length, $info.LastWriteTime)
    } else {
        Write-Host ("  {0,-28} (absent)" -f $p)
    }
}

exit $analyzerExit
