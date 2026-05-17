# ============================================================
# fix_hermes_64k.ps1  (v2)
#
# End-to-end fix for Hermes Agent's "compression model context window
# below 64,000" startup error.
#
# What this script does, in order, idempotently:
#   1. Refuse to run unless mock-mode env vars are correct.
#   2. Verify ollama + hermes on PATH.
#   3. Verify the base model is pulled.
#   4. Build OR rebuild the 64K Ollama tag. The rebuild is forced
#      automatically if 'ollama show' reports a num_ctx that does
#      NOT match the requested value (-NumCtx). Force unconditionally
#      with -ForceRebuildTag.
#   5. Verify the new tag by parsing the integer out of 'ollama show'
#      (not a substring match) and asserting it equals -NumCtx.
#   6. Update the Hermes config via 'hermes config set' for BOTH
#      keys:
#        auxiliary.compression.model          = <new tag>
#        auxiliary.compression.context_length = <NumCtx>
#      The previous version edited the YAML with PowerShell
#      Set-Content, which once corrupted the file around byte
#      offset 12201; v2 never writes the file by hand and never
#      falls back to raw text edit. If 'hermes config set' rejects
#      the nested key path, v2 ABORTS with clear manual
#      instructions instead of risking another corruption.
#   7. Re-read 'hermes config' and assert the user override is
#      visible (i.e. config.yaml is no longer "Falling back to
#      default config").
#
# Touches NOTHING in the trading project, NOTHING related to
# brokers, NOTHING about LIVE_TRADING. Pure LLM-side plumbing.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\fix_hermes_64k.ps1
#
# Optional:
#   -BaseModel        <name>   default qwen2.5:32b-instruct-q4_K_M
#   -NewTagName       <name>   default qwen2.5-32b-instruct-q4_K_M-64k
#   -NumCtx           <int>    default 65536; lower to 49152/40960 on OOM
#   -HermesConfig     <path>   default $env:LOCALAPPDATA\hermes\config.yaml
#   -ForceRebuildTag           always rm + create, ignore current num_ctx
#   -DryRun                    print what would change, change nothing
# ============================================================

[CmdletBinding()]
param(
    [string]$BaseModel        = "qwen2.5:32b-instruct-q4_K_M",
    [string]$NewTagName       = "qwen2.5-32b-instruct-q4_K_M-64k",
    [int]   $NumCtx           = 65536,
    [string]$HermesConfig     = "$env:LOCALAPPDATA\hermes\config.yaml",
    [switch]$ForceRebuildTag,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Step { param([string]$T) Write-Host ""; Write-Host ("=" * 60) -ForegroundColor Cyan; Write-Host "  $T" -ForegroundColor Cyan; Write-Host ("=" * 60) -ForegroundColor Cyan }
function Info { param([string]$T) Write-Host "  $T" }
function Warn { param([string]$T) Write-Host "  ! $T" -ForegroundColor Yellow }
function Fail { param([string]$T) Write-Host "ABORT: $T" -ForegroundColor Red; exit 1 }
function Ok   { param([string]$T) Write-Host "  [OK] $T" -ForegroundColor Green }

# ---------- Pre-flight safety: refuse if mock-mode envelope is wrong ----------
if ($env:LIVE_TRADING -and $env:LIVE_TRADING -ne "false") { Fail "LIVE_TRADING=$($env:LIVE_TRADING) -- this script is mock-mode only." }
if ($env:EXECUTION_MODE -and $env:EXECUTION_MODE -ne "mock") { Fail "EXECUTION_MODE=$($env:EXECUTION_MODE) -- must be 'mock'." }
if ($env:BROKER_MODE -and $env:BROKER_MODE -ne "mock") { Fail "BROKER_MODE=$($env:BROKER_MODE) -- must be 'mock'." }

# ---------- 0. Sanity ----------
Step "0. Sanity"
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) { Fail "ollama not on PATH" }
if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) { Fail "hermes not on PATH" }
Info ("ollama : " + ((ollama --version 2>&1) -join " "))
Info ("hermes : " + ((hermes --version 2>&1) -join " "))
Info ("DryRun           : $DryRun")
Info ("ForceRebuildTag  : $ForceRebuildTag")
Info ("BaseModel        : $BaseModel")
Info ("NewTagName       : $NewTagName")
Info ("NumCtx           : $NumCtx")
Info ("HermesConfig     : $HermesConfig")

# ---------- 1. Confirm base model is pulled ----------
Step "1. Confirm base model '$BaseModel' is pulled"
$list = (ollama list 2>&1 | Out-String)
if ($list -notmatch [regex]::Escape($BaseModel)) {
    Fail "Base model '$BaseModel' not in 'ollama list'. Pull it first:`n    ollama pull $BaseModel"
}
Ok "base model found"

# ---------- Helper: parse num_ctx from `ollama show` output ----------
function Get-Ollama-NumCtx {
    param([string]$Tag)
    $raw = (ollama show $Tag 2>&1 | Out-String)
    $m = [regex]::Match($raw, 'num_ctx\s+(\d+)')
    if ($m.Success) { return [int]$m.Groups[1].Value }
    # Some versions print "context length        65536"
    $m2 = [regex]::Match($raw, '(?i)context[\s_]*length\s+(\d+)')
    if ($m2.Success) { return [int]$m2.Groups[1].Value }
    return $null
}

# ---------- 2. Build or rebuild the 64K tag ----------
Step "2. Ensure tag '$NewTagName' exists with num_ctx=$NumCtx"
$tagExists = ($list -match [regex]::Escape($NewTagName))
$needsRebuild = $true

if ($tagExists -and -not $ForceRebuildTag) {
    $detected = Get-Ollama-NumCtx -Tag $NewTagName
    if ($null -eq $detected) {
        Warn "tag exists but couldn't parse num_ctx from 'ollama show', will rebuild"
    } elseif ($detected -eq $NumCtx) {
        Ok "tag exists and num_ctx already = $NumCtx, no rebuild needed"
        $needsRebuild = $false
    } else {
        Warn "tag exists but num_ctx=$detected (expected $NumCtx), will rebuild"
    }
}

if ($needsRebuild) {
    if ($DryRun) {
        Info "DRY RUN: would remove (if present) and create '$NewTagName' from '$BaseModel' with num_ctx=$NumCtx"
    } else {
        if ($tagExists) {
            Info "removing existing tag '$NewTagName'"
            ollama rm $NewTagName | Out-Null
        }
        $backupDir = "C:\Trading\backup"
        New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
        $mf = Join-Path $backupDir "$NewTagName.Modelfile"
        # Plain ASCII, LF-style content so neither Ollama nor anything
        # downstream gets surprised by CRLF or BOM.
        $modelfileContent = "FROM $BaseModel`nPARAMETER num_ctx $NumCtx`n"
        [System.IO.File]::WriteAllText($mf, $modelfileContent, [System.Text.UTF8Encoding]::new($false))
        Info "Modelfile : $mf"
        Get-Content $mf | ForEach-Object { Info "  | $_" }
        ollama create $NewTagName -f $mf
        if ($LASTEXITCODE -ne 0) { Fail "ollama create failed (exit $LASTEXITCODE)" }
        Ok "tag rebuilt"
    }
}

# ---------- 3. Verify num_ctx is exactly $NumCtx ----------
Step "3. Verify '$NewTagName' reports num_ctx=$NumCtx"
if ($DryRun) {
    Info "DRY RUN: skipping verification"
} else {
    $detected = Get-Ollama-NumCtx -Tag $NewTagName
    if ($null -eq $detected) {
        Warn "'ollama show $NewTagName' did not contain a num_ctx field; full dump:"
        ollama show $NewTagName
        Fail "could not verify num_ctx for '$NewTagName'"
    }
    if ($detected -ne $NumCtx) {
        Fail "num_ctx mismatch: ollama reports $detected, expected $NumCtx"
    }
    Ok "ollama show '$NewTagName' confirms num_ctx=$NumCtx"
}

# ---------- 4. Update Hermes config via 'hermes config set' ----------
# v1 used PowerShell Set-Content to rewrite the YAML, which once
# corrupted the file at byte offset 12201 and forced Hermes to fall
# back to default config (silently discarding every user override).
# v2 only uses Hermes' own setter, which validates the YAML before
# writing. If it can't handle nested keys, we ABORT with manual
# instructions instead of risking another corruption.

Step "4. Patch Hermes config (auxiliary.compression.model + .context_length)"

if (-not (Test-Path $HermesConfig)) {
    Fail "Hermes config.yaml not found at $HermesConfig (override with -HermesConfig <path>)"
}

# Always make a fresh backup before any setter runs.
$ts  = Get-Date -Format "yyyyMMdd-HHmmss"
$bak = "$HermesConfig.bak.$ts"
if ($DryRun) {
    Info "DRY RUN: would back up to $bak"
} else {
    Copy-Item $HermesConfig $bak -Force
    Ok "backup written: $bak"
}

function Try-Hermes-Config-Set {
    param([string]$Key, [string]$Value)
    $tmpOut = New-TemporaryFile
    $tmpErr = New-TemporaryFile
    $p = Start-Process -FilePath (Get-Command hermes).Source `
        -ArgumentList @("config","set",$Key,$Value) `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $tmpOut `
        -RedirectStandardError  $tmpErr
    $stdout = (Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue)
    $stderr = (Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue)
    Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        ExitCode = $p.ExitCode
        StdOut   = $stdout
        StdErr   = $stderr
    }
}

function Manual-Instructions-And-Abort {
    param([string]$Reason)
    Write-Host ""
    Write-Host "Cannot apply config change automatically: $Reason" -ForegroundColor Red
    Write-Host "Do it by hand -- using Hermes' validated editor, not Set-Content:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  # 1. Set an editor that exists" -ForegroundColor Yellow
    Write-Host "  `$env:EDITOR = 'notepad'" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  # 2. Open the validated editor" -ForegroundColor Yellow
    Write-Host "  hermes config edit" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  # 3. Find this block and edit two lines:" -ForegroundColor Yellow
    Write-Host "  auxiliary:" -ForegroundColor Yellow
    Write-Host "    compression:" -ForegroundColor Yellow
    Write-Host "      model: $NewTagName            # was the original 32K model" -ForegroundColor Yellow
    Write-Host "      context_length: $NumCtx       # ADD this line if absent" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  # 4. Save and exit. Hermes will validate on save." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  # 5. Verify the override is loaded:" -ForegroundColor Yellow
    Write-Host "  hermes config | Select-String -Pattern 'compression' -Context 0,3" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "If anything looks wrong, restore the backup we just made:" -ForegroundColor Yellow
    Write-Host "  Copy-Item '$bak' '$HermesConfig' -Force" -ForegroundColor Yellow
    exit 1
}

if ($DryRun) {
    Info "DRY RUN: would run:"
    Info "  hermes config set auxiliary.compression.model $NewTagName"
    Info "  hermes config set auxiliary.compression.context_length $NumCtx"
} else {
    Info "calling: hermes config set auxiliary.compression.model $NewTagName"
    $r1 = Try-Hermes-Config-Set -Key "auxiliary.compression.model" -Value $NewTagName
    if ($r1.ExitCode -ne 0) {
        Write-Host "  exit=$($r1.ExitCode)" -ForegroundColor Red
        if ($r1.StdOut) { Write-Host "  stdout: $($r1.StdOut.Trim())" }
        if ($r1.StdErr) { Write-Host "  stderr: $($r1.StdErr.Trim())" }
        Manual-Instructions-And-Abort -Reason "hermes config set rejected the nested key 'auxiliary.compression.model'"
    }
    Ok "auxiliary.compression.model set"

    Info "calling: hermes config set auxiliary.compression.context_length $NumCtx"
    $r2 = Try-Hermes-Config-Set -Key "auxiliary.compression.context_length" -Value "$NumCtx"
    if ($r2.ExitCode -ne 0) {
        Write-Host "  exit=$($r2.ExitCode)" -ForegroundColor Red
        if ($r2.StdOut) { Write-Host "  stdout: $($r2.StdOut.Trim())" }
        if ($r2.StdErr) { Write-Host "  stderr: $($r2.StdErr.Trim())" }
        Manual-Instructions-And-Abort -Reason "hermes config set rejected the nested key 'auxiliary.compression.context_length'"
    }
    Ok "auxiliary.compression.context_length set"
}

# ---------- 5. Verify by reading config back ----------
Step "5. Verify Hermes loaded the override (no 'Falling back to default config')"

if ($DryRun) {
    Info "DRY RUN: would run 'hermes config | Select-String compression'"
} else {
    $cfgOut = (hermes config 2>&1 | Out-String)
    if ($cfgOut -match '(?i)falling\s+back\s+to\s+default') {
        Write-Host $cfgOut
        Manual-Instructions-And-Abort -Reason "'hermes config' still reports it is falling back to default config -- YAML is not parsing"
    }
    $compBlock = $cfgOut -split "`r?`n" | Select-String -Pattern "compression" -Context 0,4
    if ($compBlock) {
        Write-Host ""
        Write-Host "  compression block in hermes config:"
        foreach ($l in $compBlock) { Write-Host "  $l" }
    } else {
        Warn "could not locate a 'compression' line in 'hermes config' output -- inspect manually"
    }
    if ($cfgOut -match [regex]::Escape($NewTagName)) {
        Ok "config shows model = $NewTagName"
    } else {
        Warn "config output does not literally contain '$NewTagName' -- inspect manually"
    }
    if ($cfgOut -match "context_length\s*[:=]?\s*$NumCtx") {
        Ok "config shows context_length = $NumCtx"
    } else {
        Warn "config output does not show context_length = $NumCtx -- inspect manually"
    }
}

# ---------- 6. Done ----------
Step "6. Done"
Ok "Restart Hermes now:  hermes"
Ok "Send 'hi' -- the 32,768 error should be gone and the status bar should no longer show '$ unknown'."
Write-Host ""
Write-Host "Rollback if anything misbehaves:" -ForegroundColor Yellow
Write-Host ("  Copy-Item '$bak' '$HermesConfig' -Force") -ForegroundColor Yellow
Write-Host ""
Write-Host "If you hit GPU OOM when Hermes loads the compression model," -ForegroundColor Yellow
Write-Host "rerun with a smaller context, e.g.:" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File .\scripts\fix_hermes_64k.ps1 -ForceRebuildTag -NumCtx 49152" -ForegroundColor Yellow
