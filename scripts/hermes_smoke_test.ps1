# ============================================================
# scripts/hermes_smoke_test.ps1 -- preflight for Hermes Agent
# against this repo. Pure read-only checks; does NOT launch
# Hermes, does NOT submit any order, does NOT call any broker.
#
# What it verifies (each line either [PASS] or [FAIL]):
#   1. Ollama HTTP endpoint reachable on 127.0.0.1:11434
#   2. The expected model tag exists in `ollama list`
#      (default: qwen2.5-coder:14b-64k)
#   3. The expected model tag has the required context length
#      (default: 65536)
#   4. configs/hermes.env.example pins OLLAMA_MODEL=<expected>
#   5. configs/hermes.env.example pins MODEL_CONTEXT_LENGTH=<expected>
#   6. configs/hermes.env.example sets LIVE_TRADING=false
#   7. configs/hermes.env.example sets EXECUTION_MODE=mock
#   8. configs/hermes.env.example sets BROKER_MODE=mock
#   9. Current shell env: LIVE_TRADING != truthy (or empty)
#  10. Current shell env: EXECUTION_MODE != live (or empty)
#  11. Current shell env: BROKER_MODE != live (or empty)
#  12. No forbidden broker credential env in current shell
#  13. Hermes venv python exists (does NOT invoke it; just checks path)
#  14. configs/hermes_allowlist.yaml exists in the repo
#
# Exit codes:
#   0  every check PASS
#   1  any check FAIL
#
# Read-only on production. Does NOT modify env. Does NOT touch
# config/capture.yaml. Does NOT touch logs except by reading them.
# ============================================================

param(
    [string]$ExpectedModel = "qwen2.5-coder:14b-64k",
    [int]$ExpectedContext = 65536,
    [string]$OllamaUrl = "http://127.0.0.1:11434"
)

$ErrorActionPreference = "Continue"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

$failures = 0
function Step {
    param([string]$Label, [bool]$Ok, [string]$Detail = "")
    $tag = if ($Ok) { "[PASS]" } else { "[FAIL]" ; $script:failures++ }
    if ($Detail) {
        "{0,-6} {1,-58} {2}" -f $tag, $Label, $Detail
    } else {
        "{0,-6} {1}" -f $tag, $Label
    }
}

Write-Host "hermes_smoke_test.ps1 -- preflight (does NOT launch Hermes)"
Write-Host "  projectRoot       : $projectRoot"
Write-Host "  expected model    : $ExpectedModel"
Write-Host "  expected context  : $ExpectedContext"
Write-Host "  Ollama URL        : $OllamaUrl"
Write-Host ""

# ----- 1. Ollama HTTP endpoint reachable -----
$ollamaOk = $false
try {
    $r = Invoke-WebRequest -Uri "$OllamaUrl/api/version" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
    if ($r.StatusCode -eq 200) { $ollamaOk = $true }
} catch {}
Step "1. Ollama reachable on $OllamaUrl" $ollamaOk

# ----- 2-3. ollama list contains expected model + context check -----
$modelOk = $false
$ollamaList = ""
try {
    $ollamaList = ollama list 2>&1 | Out-String
    if ($ollamaList -match [regex]::Escape($ExpectedModel)) { $modelOk = $true }
} catch {}
Step "2. Ollama list contains $ExpectedModel" $modelOk

$contextOk = $false
$actualContext = "(not checked)"
if ($modelOk) {
    # The right check is the modelfile's PARAMETER num_ctx (runtime override),
    # NOT `ollama show`'s 'context length' field. The latter shows the base
    # GGUF metadata (32768 for Qwen2.5) regardless of the runtime num_ctx
    # the operator built the -64k tag with. Ollama serves whatever num_ctx
    # the modelfile sets (Qwen2.5 supports up to 131072 via YaRN), and
    # PARAMETER num_ctx is the deterministic way to assert >= 64K.
    try {
        $modelfile = ollama show $ExpectedModel --modelfile 2>&1 | Out-String
        $m = [regex]::Match($modelfile, '(?m)^\s*PARAMETER\s+num_ctx\s+(\d+)')
        if ($m.Success) {
            $actualContext = $m.Groups[1].Value
            if ([int]$actualContext -ge $ExpectedContext) { $contextOk = $true }
        } else {
            $actualContext = "(no PARAMETER num_ctx in modelfile; base GGUF default applies)"
        }
    } catch {}
}
Step "3. ${ExpectedModel} modelfile num_ctx >= ${ExpectedContext}" $contextOk "(actual: $actualContext)"

# ----- 4-8. configs/hermes.env.example invariants -----
$envExample = Join-Path $projectRoot "configs\hermes.env.example"
$envText = if (Test-Path $envExample) { Get-Content $envExample -Raw } else { "" }

function Test-EnvLine {
    param([string]$Key, [string]$Value, [string]$Text)
    return [bool]([regex]::IsMatch($Text, "(?m)^\s*$([regex]::Escape($Key))\s*=\s*$([regex]::Escape($Value))\s*$"))
}

Step "4. hermes.env.example pins OLLAMA_MODEL=$ExpectedModel" (Test-EnvLine "OLLAMA_MODEL" $ExpectedModel $envText)
Step "5. hermes.env.example pins MODEL_CONTEXT_LENGTH=$ExpectedContext" (Test-EnvLine "MODEL_CONTEXT_LENGTH" $ExpectedContext $envText)
Step "6. hermes.env.example pins LIVE_TRADING=false" (Test-EnvLine "LIVE_TRADING" "false" $envText)
Step "7. hermes.env.example pins EXECUTION_MODE=mock" (Test-EnvLine "EXECUTION_MODE" "mock" $envText)
Step "8. hermes.env.example pins BROKER_MODE=mock"   (Test-EnvLine "BROKER_MODE"   "mock" $envText)

# ----- 9-11. Current shell envelope is NOT hot -----
function Test-ShellNotHot {
    param([string]$Key, [string]$AllowedValue)
    $v = [System.Environment]::GetEnvironmentVariable($Key, "Process")
    return (-not $v) -or ($v -eq $AllowedValue)
}

Step "9. shell env LIVE_TRADING != truthy"   (Test-ShellNotHot "LIVE_TRADING"   "false")
Step "10. shell env EXECUTION_MODE != live"  (Test-ShellNotHot "EXECUTION_MODE" "mock")
Step "11. shell env BROKER_MODE != live"     (Test-ShellNotHot "BROKER_MODE"    "mock")

# ----- 12. No forbidden broker credential env in current shell -----
$Forbidden = @(
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
    "IB_ACCOUNT", "IB_USERNAME", "IB_PASSWORD",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN"
)
$leakedCreds = @()
foreach ($name in $Forbidden) {
    if ([System.Environment]::GetEnvironmentVariable($name, "Process")) {
        $leakedCreds += $name
    }
}
$credOk = $leakedCreds.Count -eq 0
$credDetail = if ($credOk) { "(none of $($Forbidden.Count) checked)" } else { "leaked: $($leakedCreds -join ', ')" }
Step "12. no broker credential env in shell" $credOk $credDetail

# ----- 13. Hermes venv python exists -----
$hermesPy = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
$pyOk = Test-Path $hermesPy
Step "13. Hermes venv python present at expected path" $pyOk $hermesPy

# ----- 14. Allowlist file exists in the repo -----
$allowlistPath = Join-Path $projectRoot "configs\hermes_allowlist.yaml"
Step "14. configs/hermes_allowlist.yaml present in repo" (Test-Path $allowlistPath)

# ----- summary -----
Write-Host ""
Write-Host "----- summary -----"
if ($failures -eq 0) {
    Write-Host "OVERALL : PASS  (all checks green)"
    exit 0
} else {
    Write-Host ("OVERALL : FAIL  ({0} check(s) failed)" -f $failures)
    Write-Host "         See docs/hermes_operating_runbook.md sections 6 + 7 for fixes."
    exit 1
}
