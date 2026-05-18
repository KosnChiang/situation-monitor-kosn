# ============================================================
# Hermes launcher (mock-only, GPU #1, path-restricted)
#
# Run from Windows PowerShell:
#   cd C:\Trading\ai_fibo_vision_trader
#   powershell -ExecutionPolicy Bypass -File .\scripts\start_hermes.ps1
#
# Fixed version:
#   - Does NOT execute hermes.exe. Windows AppLocker / policy may block it.
#   - Launches Hermes through the installed venv Python entrypoint instead.
#   - Defaults to qwen2.5-coder:14b-64k / 65536 context for Hermes Agent.
# ============================================================

$ErrorActionPreference = "Stop"

# ----- 1. Hard mock-mode envelope (cannot be flipped from here) -----
$env:LIVE_TRADING         = "false"
$env:EXECUTION_MODE       = "mock"
$env:BROKER_MODE          = "mock"
$env:CUDA_VISIBLE_DEVICES = "1"
$env:PROJECT_ROOT         = "C:\Trading\ai_fibo_vision_trader"
$env:DATA_ROOT            = "D:\TradingData"

# ----- 2. Ollama backend defaults -----
if (-not $env:OLLAMA_BASE_URL)        { $env:OLLAMA_BASE_URL        = "http://127.0.0.1:11434" }
if (-not $env:OLLAMA_MODEL)           { $env:OLLAMA_MODEL           = "qwen2.5-coder:14b-64k" }
if (-not $env:MODEL_CONTEXT_LENGTH)   { $env:MODEL_CONTEXT_LENGTH   = "65536" }
if (-not $env:OLLAMA_NUM_CTX)         { $env:OLLAMA_NUM_CTX         = "65536" }

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
            $kv  = $line.Split("=", 2)
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

# ----- 4.1. Force known-good Hermes model/context after env overlay -----
# Hermes Agent requires >= 64K context. These old model names usually resolve to 32K.
if ($env:OLLAMA_MODEL -in @("qwen2.5:14b", "qwen2.5-coder:14b")) {
    Write-Host "[fix] OLLAMA_MODEL=$($env:OLLAMA_MODEL) is usually 32K; switching to qwen2.5-coder:14b-64k"
    $env:OLLAMA_MODEL = "qwen2.5-coder:14b-64k"
}

$env:MODEL_CONTEXT_LENGTH = "65536"
$env:OLLAMA_NUM_CTX       = "65536"

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

# ----- 6. Resolve Hermes Python entrypoint -----
# Do NOT execute hermes.exe. It may be blocked by Windows application-control policy.
if ($env:HERMES_EXEC) {
    Write-Host "[ignore] HERMES_EXEC is set but ignored to avoid Windows policy blocking hermes.exe:"
    Write-Host "         $($env:HERMES_EXEC)"
}

if (-not $env:HERMES_PY) {
    $env:HERMES_PY = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
}

if (-not (Test-Path $env:HERMES_PY)) {
    Write-Error "Hermes venv python not found: $($env:HERMES_PY)`nReinstall Hermes Agent or set HERMES_PY to the venv python.exe path."
}

# Verify the Python package entrypoint exists before launching.
& $env:HERMES_PY -c "from hermes_cli.main import main; print('Hermes Python entrypoint OK')"

# ----- 7. Echo configuration -----
Write-Host ""
Write-Host "Hermes launcher -- mock-only configuration"
Write-Host "------------------------------------------"
Write-Host "  LIVE_TRADING          = $($env:LIVE_TRADING)"
Write-Host "  EXECUTION_MODE        = $($env:EXECUTION_MODE)"
Write-Host "  BROKER_MODE           = $($env:BROKER_MODE)"
Write-Host "  CUDA_VISIBLE_DEVICES  = $($env:CUDA_VISIBLE_DEVICES)"
Write-Host "  PROJECT_ROOT          = $($env:PROJECT_ROOT)"
Write-Host "  DATA_ROOT             = $($env:DATA_ROOT)"
Write-Host "  OLLAMA_BASE_URL       = $($env:OLLAMA_BASE_URL)"
Write-Host "  OLLAMA_MODEL          = $($env:OLLAMA_MODEL)"
Write-Host "  MODEL_CONTEXT_LENGTH  = $($env:MODEL_CONTEXT_LENGTH)"
Write-Host "  OLLAMA_NUM_CTX        = $($env:OLLAMA_NUM_CTX)"
Write-Host "  HERMES_ALLOWLIST      = $($env:HERMES_ALLOWLIST)"
Write-Host "  HERMES_PY             = $($env:HERMES_PY)"
Write-Host ""

# ----- 8. Launch Hermes through Python entrypoint -----
Write-Host "Launching Hermes through Python entrypoint: hermes_cli.main"
& $env:HERMES_PY -c "from hermes_cli.main import main; raise SystemExit(main())" @args
