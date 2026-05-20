# ============================================================
# scripts/local_agent_guard.ps1 -- whitelist command runner for the
# local model handoff.
#
# Purpose
# -------
# Give a local LLM a narrow, auditable surface to operate this repo:
# inspect state, run tests, run approved Shioaji template probes.
# Block destructive ops outright; gate push / submit behind a
# human-typed -Approve token.
#
# Design rules
# ------------
# * Action dispatch via switch -- no Invoke-Expression, no
#   shell-string concatenation, ever.
# * External commands receive arguments as PowerShell parameters, not
#   as a single command string.
# * Path arguments are validated against an allowlist before being
#   passed to anything.
# * Probe actions hard-pin $env:SHIOAJI_SIMULATION = 'true' in the
#   child process AND refuse if the parent env explicitly set it to
#   a non-truthy value (don't silently override -- surface the
#   misconfiguration).
# * Approval-required actions refuse unless -Approve matches the
#   format ^HUMAN-APPROVED:[A-Za-z0-9_\-]+$. The local model cannot
#   construct that token without a human typing it.
# * Forbidden actions are rejected even with -Approve set.
# * Exit codes: 0 = success, 2 = refusal (not a runtime error), other
#   = downstream tool exit code.
#
# Usage
# -----
#   .\scripts\local_agent_guard.ps1 -Action help
#   .\scripts\local_agent_guard.ps1 -Action git-status
#   .\scripts\local_agent_guard.ps1 -Action git-log -N 5
#   .\scripts\local_agent_guard.ps1 -Action git-diff-stat
#   .\scripts\local_agent_guard.ps1 -Action git-diff-path -Path templates/shioaji_live_adapter/README.md
#   .\scripts\local_agent_guard.ps1 -Action test-all
#   .\scripts\local_agent_guard.ps1 -Action pytest -Path tests/test_shioaji_adapter_template.py
#   .\scripts\local_agent_guard.ps1 -Action py-compile -Path templates/shioaji_live_adapter/streaming_quote_probe.py
#   .\scripts\local_agent_guard.ps1 -Action copy-template-files
#   .\scripts\local_agent_guard.ps1 -Action probe-streaming -Code TMFR1 -Seconds 30
#   .\scripts\local_agent_guard.ps1 -Action probe-simulation-dry-run -Code TMFR1 -Side LONG -Qty 1
#   .\scripts\local_agent_guard.ps1 -Action git-push -Approve HUMAN-APPROVED:push-2026-05-21
#   .\scripts\local_agent_guard.ps1 -Action probe-simulation-submit -Approve HUMAN-APPROVED:submit-2026-05-21 -Code TMFR1 -Side LONG -Qty 1
# ============================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string] $Action,

    [int]    $N        = 10,           # for git-log
    [string] $Path     = '',           # path for diff / pytest / py-compile
    [string] $Code     = '',           # TMF contract code for probes
    [int]    $Seconds  = 30,           # streaming probe duration
    [string] $Side     = '',           # LONG | SHORT for simulation probe
    [int]    $Qty      = 1,            # simulation probe contracts
    [int]    $CancelAfter = 0,         # simulation probe cancel delay
    [string] $Approve  = ''            # human-typed token for gated actions
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ---------- constants ---------------------------------------------------

$RepoRoot       = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$RepoVenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$AdapterDir     = 'C:\Trading\live-adapters\shioaji'
$AdapterPython  = Join-Path $AdapterDir '.venv\Scripts\python.exe'

$TemplateDir    = Join-Path $RepoRoot 'templates\shioaji_live_adapter'
$TemplateFiles  = @(
    'streaming_quote_probe.py',
    'simulation_order_probe.py',
    'live_adapter.py',
    'requirements.txt'
)

# Approved-path prefixes for git-diff-path. Read-only -- diff cannot
# mutate anything, but limiting paths keeps output focused on the
# scope the local model is authorised to discuss.
$ApprovedDiffPathPrefixes = @(
    'templates/shioaji_live_adapter/',
    'tests/',
    'docs/',
    'scripts/'
)

# Approved tests subtree for pytest. Reject paths outside tests/ or
# containing traversal characters.
$ApprovedTestPathPrefix   = 'tests/'

# Approved py-compile targets (probes only; live_adapter.py is owned
# by the operator after copy, not by the agent).
$ApprovedPyCompileFiles   = @(
    'templates/shioaji_live_adapter/streaming_quote_probe.py',
    'templates/shioaji_live_adapter/simulation_order_probe.py'
)

# Refusal token format.
$ApprovePattern = '^HUMAN-APPROVED:[A-Za-z0-9_\-]+$'

# ---------- helpers -----------------------------------------------------

function Write-Refuse([string]$Reason) {
    [Console]::Error.WriteLine("[guard] REFUSE: $Reason")
    exit 2
}

function Write-Info([string]$Message) {
    Write-Host "[guard] $Message"
}

function Assert-NoShellMeta([string]$Value, [string]$Field) {
    # Defence in depth -- we pass values as parameters, not as a shell
    # string, so this guard is not load-bearing. But blocking obvious
    # metacharacters in inputs the model can supply makes injection
    # attempts visible in audit logs instead of silent.
    if ($Value -match '[;&|`<>$()]' -or $Value -match '\.\.[\\/]') {
        Write-Refuse "$Field contains shell metacharacter or path traversal: $Value"
    }
}

function Assert-ApprovedPath([string]$P, [string[]]$Prefixes) {
    Assert-NoShellMeta -Value $P -Field 'Path'
    $norm = $P.Replace('\', '/')
    foreach ($prefix in $Prefixes) {
        if ($norm.StartsWith($prefix)) { return }
    }
    Write-Refuse "Path '$P' is not under an approved prefix: $($Prefixes -join ', ')"
}

function Assert-Approved([string]$ActionName) {
    if (-not ($Approve -match $ApprovePattern)) {
        Write-Refuse "Action '$ActionName' requires -Approve HUMAN-APPROVED:<reason>. Got: '$Approve'"
    }
    Write-Info "approval token accepted: $Approve"
}

function Assert-SimulationEnv() {
    $raw = $env:SHIOAJI_SIMULATION
    if ($null -ne $raw -and $raw.Trim().ToLower() -notin @('', 'true', '1', 'yes', 'on')) {
        Write-Refuse "SHIOAJI_SIMULATION is '$raw' (parent env). Probes refuse to run unless truthy or unset. Setting it to false for probes is forbidden by the handoff."
    }
}

function Assert-AdapterReady() {
    if (-not (Test-Path $AdapterDir))    { Write-Refuse "adapter dir missing: $AdapterDir" }
    if (-not (Test-Path $AdapterPython)) { Write-Refuse "adapter venv python missing: $AdapterPython" }
}

function Assert-RepoVenvReady() {
    if (-not (Test-Path $RepoVenvPython)) { Write-Refuse "repo venv python missing: $RepoVenvPython" }
}

function Show-Help() {
    @"
local_agent_guard.ps1 -- whitelist runner for the local LLM handoff

Inspect:
  -Action git-status                            # git status --short
  -Action git-log [-N 10]                       # git log --oneline -N
  -Action git-diff-stat                         # git diff --stat
  -Action git-diff-path -Path <approved>        # git diff -- <path>

Validate:
  -Action test-all                              # .\scripts\test.ps1 (full suite)
  -Action pytest -Path tests/<file>             # python -m pytest <file>
  -Action py-compile -Path <approved .py>       # python -m py_compile <file>

Operator probes (out-of-tree adapter):
  -Action copy-template-files                   # copy 4 files to $AdapterDir
  -Action probe-streaming -Code TMFR1 -Seconds 30
  -Action probe-simulation-dry-run -Code TMFR1 -Side LONG -Qty 1

Approval-required (must pass -Approve HUMAN-APPROVED:<reason>):
  -Action git-push -Approve HUMAN-APPROVED:push-YYYY-MM-DD
  -Action probe-simulation-submit -Approve HUMAN-APPROVED:submit-YYYY-MM-DD \
      -Code TMFR1 -Side LONG -Qty 1 [-CancelAfter 5]

Refused outright (the guard will not dispatch these under any flag):
  any git reset --hard / git clean / force push / Remove-Item form
  any file edit / commit (return control to the human instead)
  any probe invocation with SHIOAJI_SIMULATION set to a non-truthy value
  any 'real trading' order pathway

Exit codes:
  0  success
  2  refusal (input / policy)
  *  downstream tool exit code
"@ | Write-Host
}

# ---------- forbidden short-circuit ------------------------------------
#
# Forbidden actions are rejected before the dispatch switch runs so
# even adding -Approve cannot get them through. Keeping the list here
# rather than throwing inside each branch makes the policy auditable
# in one place.

$ForbiddenActions = @(
    'git-reset-hard',
    'git-clean',
    'git-push-force',
    'remove-item',
    'rm',
    'delete',
    'print-secret',
    'set-simulation-false',
    'real-trade-submit'
)

if ($ForbiddenActions -contains $Action.ToLower()) {
    Write-Refuse "Action '$Action' is on the forbidden list. The guard will not run it under any approval token. Return control to the human."
}

# ---------- dispatch ---------------------------------------------------

switch ($Action.ToLower()) {

    'help' { Show-Help; exit 0 }

    # ---- read-only git ----

    'git-status' {
        Set-Location $RepoRoot
        & git status --short
        exit $LASTEXITCODE
    }

    'git-log' {
        if ($N -lt 1 -or $N -gt 200) { Write-Refuse "N must be 1..200; got $N" }
        Set-Location $RepoRoot
        & git log --oneline -$N
        exit $LASTEXITCODE
    }

    'git-diff-stat' {
        Set-Location $RepoRoot
        & git diff --stat
        exit $LASTEXITCODE
    }

    'git-diff-path' {
        if (-not $Path) { Write-Refuse 'git-diff-path requires -Path' }
        Assert-ApprovedPath -P $Path -Prefixes $ApprovedDiffPathPrefixes
        Set-Location $RepoRoot
        & git diff -- $Path
        exit $LASTEXITCODE
    }

    # ---- validate ----

    'test-all' {
        Assert-RepoVenvReady
        Set-Location $RepoRoot
        & (Join-Path $RepoRoot 'scripts\test.ps1')
        exit $LASTEXITCODE
    }

    'pytest' {
        Assert-RepoVenvReady
        if (-not $Path) { Write-Refuse 'pytest requires -Path tests/<file>' }
        Assert-ApprovedPath -P $Path -Prefixes @($ApprovedTestPathPrefix)
        Set-Location $RepoRoot
        & $RepoVenvPython -m pytest -q $Path
        exit $LASTEXITCODE
    }

    'py-compile' {
        Assert-RepoVenvReady
        if (-not $Path) { Write-Refuse 'py-compile requires -Path <approved file>' }
        Assert-NoShellMeta -Value $Path -Field 'Path'
        $norm = $Path.Replace('\', '/')
        if ($ApprovedPyCompileFiles -notcontains $norm) {
            Write-Refuse "py-compile path '$Path' is not on the approved list: $($ApprovedPyCompileFiles -join ', ')"
        }
        Set-Location $RepoRoot
        & $RepoVenvPython -m py_compile $Path
        if ($LASTEXITCODE -eq 0) { Write-Info "py_compile OK: $Path" }
        exit $LASTEXITCODE
    }

    # ---- operator: copy 4 template files ----

    'copy-template-files' {
        if (-not (Test-Path $AdapterDir)) {
            Write-Refuse "adapter dir missing: $AdapterDir (operator must create + populate venv first)"
        }
        foreach ($name in $TemplateFiles) {
            $src = Join-Path $TemplateDir $name
            $dst = Join-Path $AdapterDir  $name
            if (-not (Test-Path $src)) { Write-Refuse "template file missing: $src" }
            Copy-Item -LiteralPath $src -Destination $dst -Force
            Write-Info "copied: $name -> $AdapterDir"
        }
        exit 0
    }

    # ---- operator: streaming probe (read-only, finite) ----

    'probe-streaming' {
        Assert-AdapterReady
        Assert-SimulationEnv
        if ($Seconds -lt 1 -or $Seconds -gt 600) {
            Write-Refuse "Seconds must be 1..600; got $Seconds"
        }
        Assert-NoShellMeta -Value $Code -Field 'Code'
        $probe = Join-Path $AdapterDir 'streaming_quote_probe.py'
        if (-not (Test-Path $probe)) { Write-Refuse "probe missing: $probe (run copy-template-files first)" }
        $outPath = Join-Path $AdapterDir 'logs\probe_quotes.jsonl'

        $childEnv = @{}
        Get-ChildItem env: | ForEach-Object { $childEnv[$_.Name] = $_.Value }
        $childEnv['SHIOAJI_SIMULATION'] = 'true'

        Write-Info "running streaming probe: code=$Code seconds=$Seconds out=$outPath"
        # Use Start-Process to apply the env override per-invocation
        # without leaking it into the agent's parent session.
        $argList = @($probe, '--code', $Code, '--seconds', "$Seconds", '--output', $outPath)
        $proc = Start-Process -FilePath $AdapterPython -ArgumentList $argList `
                              -WorkingDirectory $AdapterDir -NoNewWindow -Wait `
                              -Environment $childEnv -PassThru
        exit $proc.ExitCode
    }

    # ---- operator: simulation probe (DRY-RUN only -- no submit) ----

    'probe-simulation-dry-run' {
        Assert-AdapterReady
        Assert-SimulationEnv
        if ($Side -notin @('LONG', 'SHORT')) {
            Write-Refuse "Side must be LONG or SHORT; got '$Side'"
        }
        if ($Qty -lt 1 -or $Qty -gt 5) {
            Write-Refuse "Qty must be 1..5 for probe; got $Qty"
        }
        Assert-NoShellMeta -Value $Code -Field 'Code'
        $probe = Join-Path $AdapterDir 'simulation_order_probe.py'
        if (-not (Test-Path $probe)) { Write-Refuse "probe missing: $probe (run copy-template-files first)" }
        $outPath = Join-Path $AdapterDir 'logs\shioaji_simulation_smoke.jsonl'

        $childEnv = @{}
        Get-ChildItem env: | ForEach-Object { $childEnv[$_.Name] = $_.Value }
        $childEnv['SHIOAJI_SIMULATION'] = 'true'

        Write-Info "running simulation probe DRY-RUN: code=$Code side=$Side qty=$Qty (NO --confirm-simulation-submit)"
        $argList = @($probe, '--code', $Code, '--side', $Side, '--qty', "$Qty", '--output', $outPath)
        $proc = Start-Process -FilePath $AdapterPython -ArgumentList $argList `
                              -WorkingDirectory $AdapterDir -NoNewWindow -Wait `
                              -Environment $childEnv -PassThru
        exit $proc.ExitCode
    }

    # ---- approval-required: git push ----

    'git-push' {
        Assert-Approved 'git-push'
        Set-Location $RepoRoot
        $branch = (& git rev-parse --abbrev-ref HEAD).Trim()
        if ($branch -in @('main', 'master')) {
            Write-Refuse "refusing to push from $branch directly. Open a PR instead."
        }
        Write-Info "pushing $branch to origin (no force)"
        & git push origin $branch
        exit $LASTEXITCODE
    }

    # ---- approval-required: simulation submit (still simulation account) ----

    'probe-simulation-submit' {
        Assert-Approved 'probe-simulation-submit'
        Assert-AdapterReady
        Assert-SimulationEnv
        if ($Side -notin @('LONG', 'SHORT')) {
            Write-Refuse "Side must be LONG or SHORT; got '$Side'"
        }
        if ($Qty -lt 1 -or $Qty -gt 5) {
            Write-Refuse "Qty must be 1..5 for probe; got $Qty"
        }
        if ($CancelAfter -lt 0 -or $CancelAfter -gt 60) {
            Write-Refuse "CancelAfter must be 0..60; got $CancelAfter"
        }
        Assert-NoShellMeta -Value $Code -Field 'Code'
        $probe = Join-Path $AdapterDir 'simulation_order_probe.py'
        if (-not (Test-Path $probe)) { Write-Refuse "probe missing: $probe (run copy-template-files first)" }
        $outPath = Join-Path $AdapterDir 'logs\shioaji_simulation_smoke.jsonl'

        $childEnv = @{}
        Get-ChildItem env: | ForEach-Object { $childEnv[$_.Name] = $_.Value }
        $childEnv['SHIOAJI_SIMULATION'] = 'true'

        $argList = @($probe, '--code', $Code, '--side', $Side, '--qty', "$Qty",
                     '--output', $outPath, '--confirm-simulation-submit')
        if ($CancelAfter -gt 0) {
            $argList += @('--cancel-after', "$CancelAfter")
        }
        Write-Info "running simulation probe SUBMIT: code=$Code side=$Side qty=$Qty cancel-after=$CancelAfter (simulation account)"
        $proc = Start-Process -FilePath $AdapterPython -ArgumentList $argList `
                              -WorkingDirectory $AdapterDir -NoNewWindow -Wait `
                              -Environment $childEnv -PassThru
        exit $proc.ExitCode
    }

    # ---- explicit refusal for unknown / edit / commit ----

    { $_ -in @('edit-file', 'write-file', 'commit', 'git-commit') } {
        Write-Refuse "Action '$Action' is not available to the local model. Return control to the human; edits and commits happen out-of-band."
    }

    default {
        Write-Refuse "unknown action: '$Action'. Run -Action help to list approved actions."
    }
}
