# ============================================================
# diagnose.ps1 -- Windows 11 host diagnostic for the trading box
#
# Purpose: collect every fact a downstream Claude Code session needs
# to decide what to do, WITHOUT modifying anything on this machine.
# Read-only by design.
#
# Run from the project root:
#   cd C:\Trading\ai_fibo_vision_trader
#   powershell -ExecutionPolicy Bypass -File .\scripts\diagnose.ps1
#
# Output:
#   * pretty summary printed to stdout
#   * machine-readable copy at logs\diagnose.json
# ============================================================

$ErrorActionPreference = "Continue"   # never abort -- we want partial info
$ProgressPreference   = "SilentlyContinue"

function Try-Run {
    param([string]$Cmd, [string[]]$Args = @())
    $result = [ordered]@{
        command   = "$Cmd $($Args -join ' ')".Trim()
        found     = $false
        exit_code = $null
        stdout    = $null
        stderr    = $null
    }
    $resolved = Get-Command $Cmd -ErrorAction SilentlyContinue
    if (-not $resolved) { return [PSCustomObject]$result }
    $result.found = $true
    try {
        $tmpOut = New-TemporaryFile
        $tmpErr = New-TemporaryFile
        $p = Start-Process -FilePath $resolved.Source -ArgumentList $Args `
            -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
        $result.exit_code = $p.ExitCode
        $result.stdout = (Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue)
        $result.stderr = (Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue)
        Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
    } catch {
        $result.stderr = $_.Exception.Message
    }
    return [PSCustomObject]$result
}

function Try-Tcp {
    param([string]$HostName, [int]$Port)
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1500, $false)
        if ($ok -and $c.Connected) { $c.Close(); return $true }
        $c.Close(); return $false
    } catch { return $false }
}

# ---------- 0. Sanity ----------
$here = $PSScriptRoot ? (Split-Path $PSScriptRoot -Parent) : (Get-Location).Path
Set-Location $here

# ---------- 1. Host / OS ----------
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$host_info = [ordered]@{
    os_caption        = $os.Caption
    os_version        = $os.Version
    os_build          = $os.BuildNumber
    arch              = $env:PROCESSOR_ARCHITECTURE
    is_windows        = ($PSVersionTable.Platform -eq $null -or $PSVersionTable.OS -match "Windows")
    ps_edition        = $PSVersionTable.PSEdition
    ps_version        = $PSVersionTable.PSVersion.ToString()
    cpu               = (Get-CimInstance Win32_Processor | Select-Object -First 1).Name
    cores_logical     = $cs.NumberOfLogicalProcessors
    ram_gb            = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
    cwd               = $here
}

# ---------- 2. GPU ----------
$gpu_info = [ordered]@{
    via_wmi    = @(Get-CimInstance Win32_VideoController | ForEach-Object {
        [ordered]@{
            name        = $_.Name
            driver      = $_.DriverVersion
            adapter_ram = if ($_.AdapterRAM) { [math]::Round($_.AdapterRAM / 1GB, 2) } else { $null }
        }
    })
    nvidia_smi = Try-Run "nvidia-smi" @("--query-gpu=index,name,memory.total,memory.used,driver_version,compute_cap","--format=csv,noheader")
}

# ---------- 3. Project paths ----------
$project = [ordered]@{
    project_root_exists   = Test-Path "C:\Trading\ai_fibo_vision_trader"
    configs_dir_exists    = Test-Path "C:\Trading\configs"
    logs_dir_exists       = Test-Path "C:\Trading\logs"
    data_root_exists      = Test-Path "D:\TradingData"
    venv_exists           = Test-Path ".\.venv\Scripts\python.exe"
    venv_python           = if (Test-Path ".\.venv\Scripts\python.exe") { (Resolve-Path ".\.venv\Scripts\python.exe").Path } else { $null }
    requirements_present  = Test-Path ".\requirements.txt"
    git_branch            = (Try-Run "git" @("rev-parse","--abbrev-ref","HEAD")).stdout.Trim()
    git_head              = (Try-Run "git" @("rev-parse","HEAD")).stdout.Trim()
    git_dirty             = ((Try-Run "git" @("status","--porcelain")).stdout).Trim().Length -gt 0
}

# ---------- 4. Mock-mode env ----------
$env_check = [ordered]@{
    LIVE_TRADING         = $env:LIVE_TRADING
    EXECUTION_MODE       = $env:EXECUTION_MODE
    BROKER_MODE          = $env:BROKER_MODE
    CUDA_VISIBLE_DEVICES = $env:CUDA_VISIBLE_DEVICES
    is_mock_only         = ($env:LIVE_TRADING -in @($null,"","false")) `
                             -and ($env:EXECUTION_MODE -in @($null,"","mock")) `
                             -and ($env:BROKER_MODE -in @($null,"","mock"))
    forbidden_creds      = @{}
}
foreach ($name in @(
    "SHIOAJI_API_KEY","IB_USERNAME","IB_PASSWORD","MT5_LOGIN","MT5_PASSWORD",
    "BINANCE_API_KEY","BINANCE_API_SECRET","ALPACA_API_KEY","ALPACA_SECRET_KEY",
    "CTPRO_USER","CTPRO_PASSWORD","CTPRO_TOKEN"
)) {
    $v = [System.Environment]::GetEnvironmentVariable($name)
    if ($v) { $env_check.forbidden_creds[$name] = "<SET>" }
}

# ---------- 5. Python tools.gpu_check ----------
$gpu_check_py = if ($project.venv_exists) {
    Try-Run $project.venv_python @("-m","tools.gpu_check")
} else { @{ found=$false; stderr="venv not built yet" } }

# ---------- 6. Hermes ----------
$hermes = [ordered]@{
    on_path     = [bool](Get-Command hermes -ErrorAction SilentlyContinue)
    version     = Try-Run "hermes" @("--version")
    doctor      = Try-Run "hermes" @("doctor")
    model       = Try-Run "hermes" @("model")
    install_dir = if (Test-Path "$env:LOCALAPPDATA\hermes") { "$env:LOCALAPPDATA\hermes" } else { $null }
}

# ---------- 7. Ollama ----------
$ollama = [ordered]@{
    on_path           = [bool](Get-Command ollama -ErrorAction SilentlyContinue)
    version           = Try-Run "ollama" @("--version")
    list              = Try-Run "ollama" @("list")
    serving_11434     = Try-Tcp "127.0.0.1" 11434
    openai_endpoint   = "http://127.0.0.1:11434/v1"
    qwen25_14b_pulled = $null
}
if ($ollama.list.stdout) {
    $ollama.qwen25_14b_pulled = ($ollama.list.stdout -match "qwen2\.5:14b")
}

# ---------- 8. LM Studio ----------
$lms_candidates = @(
    "$env:LOCALAPPDATA\Programs\LM Studio\LM Studio.exe",
    "$env:LOCALAPPDATA\LMStudio\LM Studio.exe",
    "$env:ProgramFiles\LM Studio\LM Studio.exe",
    "${env:ProgramFiles(x86)}\LM Studio\LM Studio.exe"
)
$lms_found = $lms_candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
$lmstudio = [ordered]@{
    installed       = [bool]$lms_found
    install_path    = $lms_found
    cli_on_path     = [bool](Get-Command lms -ErrorAction SilentlyContinue)
    cli_version     = Try-Run "lms" @("--version")
    serving_1234    = Try-Tcp "127.0.0.1" 1234
    openai_endpoint = "http://127.0.0.1:1234/v1"
}

# ---------- 9. pytest ----------
$pytest = if ($project.venv_exists) {
    Try-Run $project.venv_python @("-m","pytest","-q")
} else { @{ found=$false; stderr="venv not built yet -- run scripts\setup_windows.ps1 first" } }

$pytest_summary = $null
if ($pytest.stdout) {
    $m = [regex]::Match($pytest.stdout, '(\d+)\s+passed')
    if ($m.Success) { $pytest_summary = "$($m.Groups[1].Value) passed" }
}

# ---------- assemble ----------
$report = [ordered]@{
    timestamp_utc  = (Get-Date).ToUniversalTime().ToString("o")
    host           = $host_info
    gpu            = $gpu_info
    project        = $project
    env_check      = $env_check
    gpu_check_py   = $gpu_check_py
    hermes         = $hermes
    ollama         = $ollama
    lmstudio       = $lmstudio
    pytest         = @{ exit_code = $pytest.exit_code; summary = $pytest_summary; stdout_tail = ($pytest.stdout -split "`n" | Select-Object -Last 8) -join "`n" }
}

# write JSON
$logs_dir = Join-Path $here "logs"
New-Item -ItemType Directory -Force -Path $logs_dir | Out-Null
$json_path = Join-Path $logs_dir "diagnose.json"
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $json_path -Encoding UTF8

# pretty summary
Write-Host ""
Write-Host "=========================================================="
Write-Host " Windows trading-box diagnostic"
Write-Host "=========================================================="
Write-Host ("  OS                  : {0} (build {1})" -f $host_info.os_caption, $host_info.os_build)
Write-Host ("  PowerShell          : {0} {1}" -f $host_info.ps_edition, $host_info.ps_version)
Write-Host ("  CPU / RAM           : {0}  /  {1} GB" -f $host_info.cpu, $host_info.ram_gb)
Write-Host ("  GPU (WMI count)     : {0}" -f $gpu_info.via_wmi.Count)
foreach ($g in $gpu_info.via_wmi) {
    Write-Host ("    - {0}  driver={1}" -f $g.name, $g.driver)
}
Write-Host ("  nvidia-smi found    : {0}" -f $gpu_info.nvidia_smi.found)
Write-Host ""
Write-Host ("  cwd                 : {0}" -f $host_info.cwd)
Write-Host ("  git branch / dirty  : {0}  dirty={1}" -f $project.git_branch, $project.git_dirty)
Write-Host ("  .venv present       : {0}" -f $project.venv_exists)
Write-Host ("  data root D:\TradingData exists : {0}" -f $project.data_root_exists)
Write-Host ""
Write-Host ("  LIVE_TRADING        : {0}" -f $env_check.LIVE_TRADING)
Write-Host ("  EXECUTION_MODE      : {0}" -f $env_check.EXECUTION_MODE)
Write-Host ("  BROKER_MODE         : {0}" -f $env_check.BROKER_MODE)
Write-Host ("  CUDA_VISIBLE_DEVICES: {0}" -f $env_check.CUDA_VISIBLE_DEVICES)
Write-Host ("  is_mock_only        : {0}" -f $env_check.is_mock_only)
if ($env_check.forbidden_creds.Count -gt 0) {
    Write-Host ("  ! FORBIDDEN CREDS in env: {0}" -f ($env_check.forbidden_creds.Keys -join ", "))
}
Write-Host ""
Write-Host ("  hermes on PATH      : {0}" -f $hermes.on_path)
if ($hermes.version.stdout) { Write-Host ("    version           : {0}" -f $hermes.version.stdout.Trim()) }
Write-Host ("  ollama on PATH      : {0}    serving 11434: {1}    qwen2.5:14b pulled: {2}" -f $ollama.on_path, $ollama.serving_11434, $ollama.qwen25_14b_pulled)
Write-Host ("  LM Studio installed : {0}    serving 1234: {1}" -f $lmstudio.installed, $lmstudio.serving_1234)
if ($lmstudio.install_path) { Write-Host ("    install path      : {0}" -f $lmstudio.install_path) }
Write-Host ""
Write-Host ("  pytest summary      : {0}" -f $pytest_summary)
Write-Host ""
Write-Host ("Full JSON written to : {0}" -f $json_path)
Write-Host "Paste either the JSON or this summary back to your Claude Code session."
Write-Host "=========================================================="
