# ============================================================
# Hermes launcher (mock-only, GPU #1, path-restricted)
#
# Run from Windows PowerShell:
#   cd C:\Trading\ai_fibo_vision_trader
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_hermes.ps1
#
# This script does NOT install Hermes and does NOT guess where it lives.
# Set HERMES_EXEC in C:\Trading\configs\hermes.env (or your shell env)
# to the absolute path of the Hermes binary / Python entry point you
# have personally verified. Until that is set, this script refuses to
# launch anything.
# ============================================================

$ErrorActionPreference = "Stop"

# ----- 1. Hard mock-mode envelope (cannot be flipped from here) -----
$env:LIVE_TRADING       = "false"
$env:EXECUTION_MODE     = "mock"
$env:BROKER_MODE        = "mock"
$env:CUDA_VISIBLE_DEVICES = "1"
$env:PROJECT_ROOT       = "C:\Trading\ai_fibo_vision_trader"
$env:DATA_ROOT          = "D:\TradingData"

# ----- 2. Ollama backend defaults -----
if (-not $env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL = "http://127.0.0.1:11434" }
if (-not $env:OLLAMA_MODEL)    { $env:OLLAMA_MODEL    = "qwen2.5:14b" }

# ----- 3. Path allowlist (required) -----
$AllowlistPath = "C:\Trading\configs\hermes_allowlist.yaml"
if (-not (Test-Path $AllowlistPath)) {
    Write-Error "Allowlist missing: $AllowlistPath`nCopy configs\hermes_allowlist.yaml from this repo to that path first."
}
$env:HERMES_ALLOWLIST = $AllowlistPath

# ----- 4. Optional env overlay (user-controlled) -----
$EnvFile = "C:\Trading\configs\hermes.env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $kv = $line.Split("=", 2)
            $key = $kv[0].Trim()
            $val = $kv[1].Trim().Trim('"')
            # Refuse anything that tries to flip mock mode from the env file.
            if ($key -in @("LIVE_TRADING", "EXECUTION_MODE", "BROKER_MODE")) {
                Write-Host "[skip] $EnvFile sets $key=$val -- ignored (mock-only build)"
                return
            }
            [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

# ----- 5. Final safety re-check -----
foreach ($pair in @(
    @{ k = "LIVE_TRADING";   want = "false" },
    @{ k = "EXECUTION_MODE"; want = "mock"  },
    @{ k = "BROKER_MODE";    want = "mock"  }
)) {
    $got = [System.Environment]::GetEnvironmentVariable($pair.k, "Process")
    if ($got -ne $pair.want) {
        Write-Error "Refusing to start: $($pair.k) must be '$($pair.want)', got '$got'."
    }
}

# Forbid any well-known broker / credential env from leaking through.
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
        Write-Error "Refusing to start: forbidden broker credential env '$name' is set in this session."
    }
}

# ----- 6. Echo configuration -----
Write-Host ""
Write-Host "Hermes launcher -- mock-only configuration"
Write-Host "------------------------------------------"
Write-Host "  LIVE_TRADING        = $($env:LIVE_TRADING)"
Write-Host "  EXECUTION_MODE      = $($env:EXECUTION_MODE)"
Write-Host "  BROKER_MODE         = $($env:BROKER_MODE)"
Write-Host "  CUDA_VISIBLE_DEVICES= $($env:CUDA_VISIBLE_DEVICES)"
Write-Host "  PROJECT_ROOT        = $($env:PROJECT_ROOT)"
Write-Host "  DATA_ROOT           = $($env:DATA_ROOT)"
Write-Host "  OLLAMA_BASE_URL     = $($env:OLLAMA_BASE_URL)"
Write-Host "  OLLAMA_MODEL        = $($env:OLLAMA_MODEL)"
Write-Host "  HERMES_ALLOWLIST    = $($env:HERMES_ALLOWLIST)"
Write-Host ""

# ----- 7. Launch Hermes -----
# Resolution order for the entry point:
#   1. $env:HERMES_EXEC explicitly set (in hermes.env or shell)  -> use that
#   2. `hermes` command found on PATH                            -> use that
#   3. Neither -> print configuration and exit 0 without running anything
if (-not $env:HERMES_EXEC) {
    $cmd = Get-Command hermes -ErrorAction SilentlyContinue
    if ($cmd) {
        $env:HERMES_EXEC = $cmd.Source
        Write-Host "Resolved hermes from PATH: $($env:HERMES_EXEC)"
    }
}

if (-not $env:HERMES_EXEC) {
    Write-Host "HERMES_EXEC is not set and 'hermes' is not on PATH. Nothing launched."
    Write-Host "Install Hermes Agent first:"
    Write-Host "  irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1 | iex"
    Write-Host "Then re-open PowerShell and re-run this script."
    exit 0
}

if (-not (Test-Path $env:HERMES_EXEC)) {
    Write-Error "HERMES_EXEC points to a non-existent path: $($env:HERMES_EXEC)"
}

Write-Host "Launching Hermes: $($env:HERMES_EXEC)"
# Hermes Agent's documented CLI does not take an --allowlist flag; pass
# the allowlist path through env only so a future system-prompt / config
# step can pick it up. Forward any extra args from the caller.
& $env:HERMES_EXEC @args
