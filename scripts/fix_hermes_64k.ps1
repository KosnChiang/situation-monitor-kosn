# ============================================================
# fix_hermes_64k.ps1
#
# End-to-end fix for Hermes Agent's "compression model context window
# below 64,000" startup error. Idempotent. Only touches:
#   * one new Ollama tag (built locally from an already-pulled model)
#   * Hermes' user config.yaml (backed up first)
#
# Touches NOTHING in the trading project, NOTHING related to brokers,
# NOTHING about LIVE_TRADING. Pure LLM-side plumbing.
#
# Usage (from anywhere, but the repo root is fine):
#   powershell -ExecutionPolicy Bypass -File .\scripts\fix_hermes_64k.ps1
#
# Optional:
#   -BaseModel    <name>   default qwen2.5:32b-instruct-q4_K_M
#   -NewTagName   <name>   default qwen2.5-32b-instruct-q4_K_M-64k
#   -NumCtx       <int>    default 65536; lower to 49152 / 40960 if OOM
#   -HermesConfig <path>   default $env:LOCALAPPDATA\hermes\config.yaml
#   -DryRun                show what would happen, change nothing
# ============================================================

[CmdletBinding()]
param(
    [string]$BaseModel    = "qwen2.5:32b-instruct-q4_K_M",
    [string]$NewTagName   = "qwen2.5-32b-instruct-q4_K_M-64k",
    [int]   $NumCtx       = 65536,
    [string]$HermesConfig = "$env:LOCALAPPDATA\hermes\config.yaml",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Step  { param([string]$T) Write-Host ""; Write-Host ("=" * 60) -ForegroundColor Cyan; Write-Host "  $T" -ForegroundColor Cyan; Write-Host ("=" * 60) -ForegroundColor Cyan }
function Info  { param([string]$T) Write-Host "  $T" }
function Warn  { param([string]$T) Write-Host "  ! $T" -ForegroundColor Yellow }
function Fail  { param([string]$T) Write-Host "ABORT: $T" -ForegroundColor Red; exit 1 }
function Ok    { param([string]$T) Write-Host "  [OK] $T" -ForegroundColor Green }

# Refuse to run if anything looks like live trading is being set up
if ($env:LIVE_TRADING -and $env:LIVE_TRADING -ne "false") { Fail "LIVE_TRADING=$($env:LIVE_TRADING) -- this script is mock-mode only." }

# ---------- 0. Sanity ----------
Step "0. Sanity"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Fail "ollama not on PATH" }
if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { Fail "hermes not on PATH" }
Info ("ollama : " + ((ollama --version 2>&1) -join " "))
Info ("hermes : " + ((hermes --version 2>&1) -join " "))
Info ("DryRun : $DryRun")

# ---------- 1. Confirm base model is pulled ----------
Step "1. Confirm base model '$BaseModel' is pulled"
$list = (ollama list 2>&1 | Out-String)
if ($list -notmatch [regex]::Escape($BaseModel)) {
    Fail "Base model '$BaseModel' not in 'ollama list'. Pull it first: ollama pull $BaseModel"
}
Ok "base model found"

# ---------- 2. Build (or reuse) the 64K tag ----------
Step "2. Build or reuse tag '$NewTagName' (num_ctx=$NumCtx)"
if ($list -match [regex]::Escape($NewTagName)) {
    Ok "tag '$NewTagName' already exists, skipping create"
} else {
    if ($DryRun) {
        Info "DRY RUN: would create $NewTagName from $BaseModel with num_ctx=$NumCtx"
    } else {
        $backupDir = "C:\Trading\backup"
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $mf = Join-Path $backupDir "$NewTagName.Modelfile"
        @"
FROM $BaseModel
PARAMETER num_ctx $NumCtx
"@ | Set-Content -Path $mf -Encoding ASCII
        Info "Modelfile : $mf"
        ollama create $NewTagName -f $mf
        if ($LASTEXITCODE -ne 0) { Fail "ollama create failed (exit $LASTEXITCODE)" }
        Ok "tag built"
    }
}

# ---------- 3. Verify num_ctx ----------
Step "3. Verify '$NewTagName' really reports num_ctx=$NumCtx"
$show = (ollama show $NewTagName 2>&1 | Out-String)
$matches_lines = $show -split "`r?`n" | Select-String -Pattern "num_ctx|context length|context_length"
if ($matches_lines) {
    foreach ($l in $matches_lines) { Info $l.ToString().Trim() }
}
if ($show -match "\b$NumCtx\b") {
    Ok "num_ctx=$NumCtx confirmed in ollama show output"
} else {
    Warn "could not literally find '$NumCtx' in 'ollama show' -- inspect manually:"
    Write-Host $show
}

# ---------- 4. Locate and back up Hermes config.yaml ----------
Step "4. Back up Hermes config.yaml"
if (-not (Test-Path $HermesConfig)) {
    Fail "Hermes config.yaml not found at $HermesConfig (override with -HermesConfig <path>)"
}
$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$bak = "$HermesConfig.bak.$ts"
if ($DryRun) {
    Info "DRY RUN: would back up to $bak"
} else {
    Copy-Item $HermesConfig $bak
    Ok "backup written: $bak"
}

# ---------- 5. Patch auxiliary.compression.model in YAML ----------
Step "5. Patch auxiliary.compression.model"
$lines  = Get-Content $HermesConfig
$inAux  = $false
$inComp = $false
$auxIndent  = $null
$compIndent = $null
$patched    = $false
$out = New-Object System.Collections.ArrayList

foreach ($line in $lines) {
    $indent = if ($line -match '^(\s*)') { $matches[1].Length } else { 0 }

    if ($line -match '^auxiliary:\s*$') {
        $inAux = $true; $inComp = $false; $auxIndent = $indent
        [void]$out.Add($line); continue
    }
    if ($inAux -and $line.Trim() -ne "" -and $indent -le $auxIndent) {
        $inAux = $false; $inComp = $false
    }
    if ($inAux -and $line -match '^\s+compression:\s*$') {
        $inComp = $true; $compIndent = $indent
        [void]$out.Add($line); continue
    }
    if ($inComp -and $line.Trim() -ne "" -and $indent -le $compIndent) {
        $inComp = $false
    }
    if ($inComp -and $line -match '^(\s+)model:\s*(.*)$') {
        $leading = $matches[1]
        $oldVal  = $matches[2].Trim().Trim('"').Trim("'")
        if ($oldVal -eq $NewTagName) {
            Info "model already points at '$NewTagName' -- no change needed"
            [void]$out.Add($line)
        } else {
            $newLine = "${leading}model: $NewTagName"
            Info ("old : " + $line.TrimEnd())
            Info ("new : " + $newLine)
            [void]$out.Add($newLine)
        }
        $patched = $true
        continue
    }
    [void]$out.Add($line)
}

if (-not $patched) {
    Fail "Could not find 'auxiliary.compression.model:' under '$HermesConfig'. Structure is not what we expected; nothing changed."
}

if ($DryRun) {
    Info "DRY RUN: not writing file"
} else {
    $out | Set-Content -Path $HermesConfig -Encoding UTF8
    Ok "wrote $HermesConfig"
}

# ---------- 6. Verify final state ----------
Step "6. Verify final state of config.yaml"
Get-Content $HermesConfig | Select-String -Pattern "compression:" -Context 0,4 | ForEach-Object { Write-Host $_ }

# ---------- 7. Done ----------
Step "7. Done"
Ok "Restart Hermes now:  hermes"
Ok "Then say 'hi' -- the 32,768 error should be gone."
Write-Host ""
Write-Host "Rollback if anything misbehaves:" -ForegroundColor Yellow
Write-Host ("  Copy-Item '$bak' '$HermesConfig' -Force") -ForegroundColor Yellow
Write-Host ""
Write-Host "If you hit GPU OOM when Hermes loads the compression model," -ForegroundColor Yellow
Write-Host "rerun with a smaller context, e.g.:" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\fix_hermes_64k.ps1 -NumCtx 49152" -ForegroundColor Yellow
