# ============================================================
# scripts/calibrate_chart_dry_run.ps1 -- operator wizard for live-
# capture chart calibration.
#
# Wraps the existing tools.calibrate_chart CLI with three pieces of
# UX that the bare CLI doesn't have:
#
#   1. Refuses if the envelope is hot or any broker cred env is set.
#   2. Stages the proposed YAML to logs/calibration_proposal.log
#      (gitignored via logs/*.log) so the operator can re-read it
#      after closing the terminal.
#   3. Shows a before/after diff of the relevant config/capture.yaml
#      region and opens the overlay PNG in the default viewer so the
#      operator can eyeball the tick ladder against TradingView's
#      own price grid.
#
# Does NOT modify config/capture.yaml. Does NOT touch any production
# source. Does NOT touch any 5.B-soak file. Pure operator UX layer.
#
# Modes:
#   non-interactive:
#     .\scripts\calibrate_chart_dry_run.ps1 -PixelYHigh 200 -PriceHigh 2400 `
#                                            -PixelYLow 1900 -PriceLow 2200
#   interactive (loop):
#     .\scripts\calibrate_chart_dry_run.ps1 -Interactive
#
# The operator's final step is always manual: open config/capture.yaml,
# replace the commented calibration block with the staged YAML, save.
# This wizard's job is to make sure that block is correct BEFORE the
# operator pastes it.
# ============================================================

param(
    [int]$PixelYHigh = -1,
    [double]$PriceHigh = -1,
    [int]$PixelYLow = -1,
    [double]$PriceLow = -1,
    [string]$CapturePng = "logs\capture_test.png",
    [string]$Config = "config\capture.yaml",
    [string]$OverlayPng = "logs\calibration_debug.png",
    [string]$ProposalLog = "logs\calibration_proposal.log",
    [double]$TickSpacing = 10.0,
    [switch]$Interactive,
    [switch]$NoOpenViewer
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

# ----- 3. Pin mock envelope (defence-in-depth before invoking python) ----
$env:LIVE_TRADING   = "false"
$env:EXECUTION_MODE = "mock"
$env:BROKER_MODE    = "mock"
$env:PYTHONPATH     = $projectRoot
$env:PYTHONUTF8     = "1"

# ----- 4. Verify required artifacts ---------------------------------------
if (-not (Test-Path $CapturePng)) {
    Write-Error "Capture PNG not found: $CapturePng`nHint: run ``python -m tools.capture_test`` first."
}
if (-not (Test-Path "logs")) { New-Item -ItemType Directory -Path "logs" | Out-Null }

$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

# ----- 5. Helper: validate axes locally before calling python -------------
function Test-AxisDirection {
    param(
        [int]$PyHigh, [double]$PrHigh,
        [int]$PyLow,  [double]$PrLow
    )
    if ($PyHigh -ge $PyLow) {
        return "axis_inverted: pixel_y_high ($PyHigh) must be < pixel_y_low ($PyLow); screen y grows downward."
    }
    if ($PrHigh -le $PrLow) {
        return "axis_inverted: price_high ($PrHigh) must be > price_low ($PrLow); the top label is the higher price."
    }
    return $null
}

# ----- 6. Helper: read commented calibration block from config/capture.yaml
function Get-CommentedCalibrationBlock {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return @() }
    $lines = Get-Content $Path
    $out = @()
    $capturing = $false
    foreach ($line in $lines) {
        if ($line -match '^\s*#\s*calibration\s*:') {
            $capturing = $true
        }
        if ($capturing) {
            $out += $line
            if (($out.Count -ge 2) -and ($line -notmatch '^\s*#') -and ($line.Trim() -ne '')) {
                break
            }
        }
    }
    return $out
}

# ----- 7. Helper: invoke python tools.calibrate_chart ---------------------
function Invoke-CalibrateChart {
    param(
        [int]$PyHigh, [double]$PrHigh,
        [int]$PyLow,  [double]$PrLow
    )
    $args = @(
        "-m", "tools.calibrate_chart",
        "--pixel-y-high", $PyHigh,
        "--price-high",   $PrHigh,
        "--pixel-y-low",  $PyLow,
        "--price-low",    $PrLow,
        "--input",  $CapturePng,
        "--output", $OverlayPng,
        "--tick",   $TickSpacing
    )
    $stdoutLines = & $python @args
    return @{
        ExitCode = $LASTEXITCODE
        Stdout   = ($stdoutLines | Out-String)
    }
}

# ----- 8. Helper: stage proposed YAML to log file -------------------------
function Save-Proposal {
    param([string]$YamlBlock, [string]$LogPath)
    $stamp = Get-Date -Format o
    $header = @"
# Proposed calibration block staged by scripts/calibrate_chart_dry_run.ps1
# Generated at: $stamp
# Source capture: $CapturePng
# Reference points: pixel_y_high=$PixelYHigh price_high=$PriceHigh / pixel_y_low=$PixelYLow price_low=$PriceLow
# Operator action: copy the 'calibration:' block below into config/capture.yaml,
#                  replacing the commented-out example block (lines 54-60 in the
#                  shipped config). Do NOT keep the leading '#' prefixes.
# This file is gitignored via logs/*.log; staging is non-destructive.
# ---- BEGIN PROPOSED YAML ----
$YamlBlock
# ---- END PROPOSED YAML ----
"@
    Set-Content -Path $LogPath -Value $header -Encoding utf8
}

# ----- 9. Helper: open the overlay PNG ------------------------------------
function Open-OverlayPng {
    param([string]$Path)
    if ($NoOpenViewer) {
        Write-Host "[dry-run] -NoOpenViewer set; skipping Invoke-Item $Path"
        return
    }
    if (Test-Path $Path) {
        try {
            Invoke-Item $Path
            Write-Host "[dry-run] opened $Path in default viewer (close when done)"
        } catch {
            Write-Host "[dry-run] could not open viewer ($($_.Exception.Message)); inspect $Path manually"
        }
    } else {
        Write-Host "[dry-run] overlay PNG not produced; check tools.calibrate_chart output above"
    }
}

# ----- 10. Helper: show before/after diff snippet -------------------------
function Show-ConfigDiff {
    param([string]$ProposedYaml)
    Write-Host ""
    Write-Host "----- before/after diff for $Config (READ-ONLY; wizard never modifies) -----"
    Write-Host "--- current state of $Config (calibration region) ---"
    $current = Get-CommentedCalibrationBlock -Path $Config
    if ($current.Count -gt 0) {
        $current | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "  (no '# calibration:' comment block found; see config/capture.yaml header)"
    }
    Write-Host ""
    Write-Host "--- proposed replacement (paste this into $Config) ---"
    $ProposedYaml -split "`n" | ForEach-Object { Write-Host "  $_" }
}

# ----- 11. Main flow: single iteration ------------------------------------
function Invoke-OneIteration {
    param(
        [int]$PyHigh, [double]$PrHigh,
        [int]$PyLow,  [double]$PrLow
    )
    Write-Host ""
    Write-Host "----- iteration: pixel_y_high=$PyHigh price_high=$PrHigh pixel_y_low=$PyLow price_low=$PrLow -----"

    $axisErr = Test-AxisDirection -PyHigh $PyHigh -PrHigh $PrHigh -PyLow $PyLow -PrLow $PrLow
    if ($axisErr) {
        Write-Host "[dry-run] FAIL: $axisErr"
        return 2
    }

    $r = Invoke-CalibrateChart -PyHigh $PyHigh -PrHigh $PrHigh -PyLow $PyLow -PrLow $PrLow
    if ($r.ExitCode -ne 0) {
        Write-Host "[dry-run] tools.calibrate_chart exited $($r.ExitCode); see stderr above"
        return $r.ExitCode
    }

    # Extract the YAML block (everything from 'calibration:' to end of stdout).
    $yaml = $r.Stdout
    $startIdx = $yaml.IndexOf("calibration:")
    if ($startIdx -ge 0) { $yaml = $yaml.Substring($startIdx).TrimEnd() }

    Save-Proposal -YamlBlock $yaml -LogPath $ProposalLog
    Write-Host "[dry-run] staged proposal -> $ProposalLog"

    Show-ConfigDiff -ProposedYaml $yaml
    Open-OverlayPng -Path $OverlayPng

    Write-Host ""
    Write-Host "[dry-run] next steps:"
    Write-Host "  1. Inspect $OverlayPng -- the green tick ladder must overlap TradingView's own grid."
    Write-Host "  2. If aligned: open $Config in an editor, replace the '# calibration:' commented block"
    Write-Host "     (lines 54-60 in the shipped config) with the YAML staged in $ProposalLog."
    Write-Host "     The new block must NOT keep any '#' prefix on the active lines."
    Write-Host "  3. Validate via:  python -c ""from vision.chart_calibration import ChartCalibration; print(ChartCalibration.from_yaml(r'$Config'))"""
    Write-Host "     -> should print a ChartCalibration object, NOT a CalibrationError traceback."
    Write-Host "  4. End-to-end check:  .\scripts\watch_loop_smoke.ps1  (still PASS; smoke uses tests/fixtures/* calibration, not the production file)"
    return 0
}

# ----- 12. Interactive vs one-shot flow -----------------------------------
function Read-Number {
    param([string]$Prompt, [string]$Kind = "int")
    while ($true) {
        $raw = Read-Host $Prompt
        if (-not $raw) { Write-Host "[dry-run] empty; please retry"; continue }
        if ($Kind -eq "int") {
            $i = 0
            if ([int]::TryParse($raw, [ref]$i)) { return $i }
        } else {
            $d = 0.0
            if ([double]::TryParse($raw, [ref]$d)) { return $d }
        }
        Write-Host "[dry-run] not a valid $Kind; please retry"
    }
}

Write-Host "calibrate_chart_dry_run.ps1 -- operator wizard for live-capture calibration"
Write-Host "  projectRoot : $projectRoot"
Write-Host "  capture     : $CapturePng"
Write-Host "  config (ro) : $Config"
Write-Host "  overlay     : $OverlayPng"
Write-Host "  proposal    : $ProposalLog"
Write-Host "  tick        : $TickSpacing"
Write-Host "  interactive : $($Interactive.IsPresent)"

if ($Interactive) {
    while ($true) {
        Write-Host ""
        Write-Host "----- new iteration (pick 2 price labels off $CapturePng) -----"
        $pyH = Read-Number -Prompt "pixel_y_high (row near top of chart)" -Kind int
        $prH = Read-Number -Prompt "price_high   (price at that row)"     -Kind double
        $pyL = Read-Number -Prompt "pixel_y_low  (row near bottom of chart)" -Kind int
        $prL = Read-Number -Prompt "price_low    (price at that row)"      -Kind double
        $rc = Invoke-OneIteration -PyHigh $pyH -PrHigh $prH -PyLow $pyL -PrLow $prL
        if ($rc -ne 0) {
            Write-Host "[dry-run] iteration failed (rc=$rc); retry"; continue
        }
        $ans = Read-Host "[dry-run] tick ladder aligned with TradingView grid? [y/N]"
        if ($ans -match '^[Yy]') {
            Write-Host "[dry-run] done. Proposal staged at $ProposalLog."
            exit 0
        }
    }
}

# Non-interactive: all 4 must be set.
if ($PixelYHigh -lt 0 -or $PriceHigh -lt 0 -or $PixelYLow -lt 0 -or $PriceLow -lt 0) {
    Write-Error "Non-interactive mode: -PixelYHigh / -PriceHigh / -PixelYLow / -PriceLow all required (or pass -Interactive)."
}

$rc = Invoke-OneIteration -PyHigh $PixelYHigh -PrHigh $PriceHigh -PyLow $PixelYLow -PrLow $PriceLow
exit $rc
