# ============================================================
# diagnose_windows.ps1
#
# Plain-text Windows host diagnostic. Read-only by design --
# touches nothing, installs nothing, modifies no env vars
# persistently, and never reads any broker credential.
#
# Run from the project root:
#   cd C:\Trading\ai_fibo_vision_trader
#   powershell -ExecutionPolicy Bypass -File .\scripts\diagnose_windows.ps1
#
# Output:
#   * human-readable text printed to stdout
#   * copy written to logs\windows_diagnose.txt
# ============================================================

$ErrorActionPreference = "Continue"   # never abort -- collect partial info
$ProgressPreference   = "SilentlyContinue"

# ---------- safety re-check (before doing anything else) ----------
if ($env:LIVE_TRADING -and $env:LIVE_TRADING -ne "false") {
    Write-Host "ABORT: LIVE_TRADING=$($env:LIVE_TRADING) -- this script refuses to run outside mock mode." -ForegroundColor Red
    exit 2
}
if ($env:EXECUTION_MODE -and $env:EXECUTION_MODE -ne "mock") {
    Write-Host "ABORT: EXECUTION_MODE=$($env:EXECUTION_MODE) -- not mock." -ForegroundColor Red
    exit 2
}
if ($env:BROKER_MODE -and $env:BROKER_MODE -ne "mock") {
    Write-Host "ABORT: BROKER_MODE=$($env:BROKER_MODE) -- not mock." -ForegroundColor Red
    exit 2
}

# ---------- output sink ----------
$project_root = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $project_root
$logs_dir  = Join-Path $project_root "logs"
New-Item -ItemType Directory -Force -Path $logs_dir | Out-Null
$out_path  = Join-Path $logs_dir "windows_diagnose.txt"
$lines     = New-Object System.Collections.ArrayList

function Emit {
    param([string]$Text = "")
    Write-Host $Text
    [void]$lines.Add($Text)
}

function Section {
    param([string]$Title)
    Emit ""
    Emit ("=" * 60)
    Emit "  $Title"
    Emit ("=" * 60)
}

function Run-Cmd {
    param([string]$Cmd, [string[]]$CmdArgs = @(), [int]$TimeoutSec = 30)
    $resolved = Get-Command $Cmd -ErrorAction SilentlyContinue
    if (-not $resolved) {
        Emit "  ! $Cmd : not on PATH"
        return
    }
    try {
        $tmpOut = New-TemporaryFile
        $tmpErr = New-TemporaryFile
        $p = Start-Process -FilePath $resolved.Source -ArgumentList $CmdArgs `
            -NoNewWindow -PassThru `
            -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            $p.Kill()
            Emit "  ! $Cmd timed out after $TimeoutSec s"
            return
        }
        $stdout = Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue
        $stderr = Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue
        Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
        Emit ("  cmd     : {0} {1}" -f $resolved.Source, ($CmdArgs -join " "))
        Emit ("  exit    : {0}" -f $p.ExitCode)
        if ($stdout) { Emit "  stdout  :"; ($stdout -split "`r?`n" | ForEach-Object { Emit "    $_" }) }
        if ($stderr) { Emit "  stderr  :"; ($stderr -split "`r?`n" | ForEach-Object { Emit "    $_" }) }
    } catch {
        Emit "  ! exception running ${Cmd}: $($_.Exception.Message)"
    }
}

function Try-HttpV1 {
    param([string]$BaseUrl)
    try {
        $url = "$BaseUrl/models"
        $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 -ErrorAction Stop
        Emit ("  GET {0}" -f $url)
        Emit ("  status : {0}" -f $resp.StatusCode)
        $body = $resp.Content
        if ($body.Length -gt 400) { $body = $body.Substring(0, 400) + "...(truncated)" }
        Emit ("  body   : {0}" -f $body)
        return $true
    } catch {
        Emit ("  GET {0} -> {1}" -f "$BaseUrl/models", $_.Exception.Message)
        return $false
    }
}

# ---------- 1. OS / shell / python / git ----------
Section "1. Host basics"
$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
Emit ("OS          : {0} (build {1})" -f $os.Caption, $os.BuildNumber)
Emit ("Computer    : {0}    user: {1}" -f $env:COMPUTERNAME, $env:USERNAME)
Emit ("CPU         : {0}" -f (Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name))
Emit ("Cores/RAM   : {0} logical / {1} GB" -f $cs.NumberOfLogicalProcessors, ([math]::Round($cs.TotalPhysicalMemory / 1GB, 1)))
Emit ("PowerShell  : {0} {1}" -f $PSVersionTable.PSEdition, $PSVersionTable.PSVersion)
Emit ("PS Platform : {0}" -f $PSVersionTable.Platform)
Run-Cmd "python" @("--version")
Run-Cmd "git"    @("--version")
Run-Cmd "node"   @("--version")

# ---------- 2. Project root ----------
Section "2. Project paths"
$paths = @(
    "C:\Trading\ai_fibo_vision_trader",
    "C:\Trading\configs",
    "C:\Trading\logs",
    "D:\TradingData",
    ".\.venv\Scripts\python.exe",
    ".\requirements.txt"
)
foreach ($p in $paths) {
    $exists = Test-Path $p
    Emit ("  {0,-44} exists={1}" -f $p, $exists)
}
$branch = (git rev-parse --abbrev-ref HEAD 2>$null)
$head   = (git rev-parse HEAD 2>$null)
$dirty  = ((git status --porcelain 2>$null) | Measure-Object).Count -gt 0
Emit ("  git branch  : {0}" -f $branch)
Emit ("  git HEAD    : {0}" -f $head)
Emit ("  git dirty   : {0}" -f $dirty)

# ---------- 3. scripts\test.ps1 ----------
Section "3. .\scripts\test.ps1"
if (Test-Path ".\scripts\test.ps1") {
    Run-Cmd "powershell" @("-ExecutionPolicy","Bypass","-File",".\scripts\test.ps1") -TimeoutSec 180
} else {
    Emit "  ! .\scripts\test.ps1 not found"
}

# ---------- 4. python -m tools.gpu_check ----------
Section "4. python -m tools.gpu_check (CUDA_VISIBLE_DEVICES pinned to 1)"
$prev_cvd = $env:CUDA_VISIBLE_DEVICES
$env:CUDA_VISIBLE_DEVICES = "1"
$py = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
Run-Cmd $py @("-m","tools.gpu_check")
$env:CUDA_VISIBLE_DEVICES = $prev_cvd  # restore for the rest of the diagnostic

# ---------- 5. nvidia-smi ----------
Section "5. nvidia-smi (unfiltered -- expect to see BOTH 3090s)"
Run-Cmd "nvidia-smi" @("--query-gpu=index,name,memory.total,memory.used,driver_version,compute_cap","--format=csv,noheader") -TimeoutSec 10

# ---------- 6. Hermes ----------
Section "6. Hermes Agent"
if (Get-Command hermes -ErrorAction SilentlyContinue) {
    Run-Cmd "hermes" @("--version")
    Run-Cmd "hermes" @("doctor") -TimeoutSec 60
    Run-Cmd "hermes" @("model")  -TimeoutSec 30
    if (Test-Path "$env:LOCALAPPDATA\hermes") {
        Emit ("  install dir : {0}" -f "$env:LOCALAPPDATA\hermes")
    }
} else {
    Emit "  hermes not on PATH -- not installed yet, that is fine"
}

# ---------- 7. Ollama ----------
Section "7. Ollama"
if (Get-Command ollama -ErrorAction SilentlyContinue) {
    Run-Cmd "ollama" @("--version")
    Run-Cmd "ollama" @("list") -TimeoutSec 15
    Run-Cmd "ollama" @("ps")   -TimeoutSec 15
} else {
    Emit "  ollama not on PATH -- not installed yet, that is fine"
}

# ---------- 8. LM Studio ----------
Section "8. LM Studio"
$lms_candidates = @(
    "$env:LOCALAPPDATA\Programs\LM Studio\LM Studio.exe",
    "$env:LOCALAPPDATA\LMStudio\LM Studio.exe",
    "$env:ProgramFiles\LM Studio\LM Studio.exe",
    "${env:ProgramFiles(x86)}\LM Studio\LM Studio.exe"
)
$lms_found = $lms_candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($lms_found) {
    Emit ("  LM Studio.exe found : {0}" -f $lms_found)
} else {
    Emit "  LM Studio.exe not found in any standard install path"
    foreach ($c in $lms_candidates) { Emit "    checked: $c" }
}
if (Get-Command lms -ErrorAction SilentlyContinue) {
    Run-Cmd "lms" @("--version")
} else {
    Emit "  lms CLI not on PATH"
}

# ---------- 9. Ollama OpenAI-compatible endpoint ----------
Section "9. http://127.0.0.1:11434/v1 (Ollama OpenAI-compatible)"
$ollama_v1_ok = Try-HttpV1 "http://127.0.0.1:11434/v1"

# ---------- 10. LM Studio endpoint ----------
Section "10. http://127.0.0.1:1234/v1 (LM Studio Local Server)"
$lms_v1_ok = Try-HttpV1 "http://127.0.0.1:1234/v1"

# ---------- 11. Mock-mode env recap ----------
Section "11. Mock-mode environment"
Emit ("  LIVE_TRADING          : {0}" -f $env:LIVE_TRADING)
Emit ("  EXECUTION_MODE        : {0}" -f $env:EXECUTION_MODE)
Emit ("  BROKER_MODE           : {0}" -f $env:BROKER_MODE)
Emit ("  CUDA_VISIBLE_DEVICES  : {0}" -f $env:CUDA_VISIBLE_DEVICES)

# Make sure no broker credential env is in scope. We deliberately do NOT
# read the values -- we only report whether the variable name is set.
$forbidden_cred_names = @(
    "SHIOAJI_API_KEY","SHIOAJI_SECRET_KEY",
    "IB_USERNAME","IB_PASSWORD","IB_ACCOUNT",
    "MT5_LOGIN","MT5_PASSWORD","MT5_SERVER",
    "BINANCE_API_KEY","BINANCE_API_SECRET",
    "ALPACA_API_KEY","ALPACA_SECRET_KEY",
    "CTPRO_USER","CTPRO_PASSWORD","CTPRO_TOKEN"
)
$cred_hits = @()
foreach ($n in $forbidden_cred_names) {
    if ([System.Environment]::GetEnvironmentVariable($n)) { $cred_hits += $n }
}
if ($cred_hits.Count -gt 0) {
    Emit ("  ! WARNING: forbidden credential env names are SET (values not read): {0}" -f ($cred_hits -join ", "))
} else {
    Emit  "  no broker credential env names are set in this session  [OK]"
}

# ---------- 12. Sandbox vs Windows detection ----------
Section "12. Sandbox red-flag check"
$sandbox_signals = @()
if (-not (Test-Path "C:\")) { $sandbox_signals += "C:\ not visible" }
if ((Get-Location).Path -like "/home/*" -or (Get-Location).Path -like "/workspace/*") {
    $sandbox_signals += "cwd looks Linux-shaped: $((Get-Location).Path)"
}
if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
    $sandbox_signals += "nvidia-smi not on PATH (expected on the trading box)"
}
if ($sandbox_signals.Count -gt 0) {
    Emit "  ! POSSIBLE SANDBOX -- not a Windows trading box:"
    foreach ($s in $sandbox_signals) { Emit "    - $s" }
} else {
    Emit "  no sandbox red flags -- looks like a real Windows host  [OK]"
}

# ---------- write file ----------
Section "diagnostic complete"
Emit ("Wrote {0}" -f $out_path)
$lines | Set-Content -Path $out_path -Encoding UTF8
