# ============================================================
# scripts/hermes_tool_bridge_smoke.ps1
#
# Read-only smoke test for the Hermes tool-call channel.
#
# What this verifies: when Hermes is asked to read AGENTS.md, does it
# actually invoke the file-read tool, or does it emit the tool-call
# JSON as text in the response body? The latter is the failure mode
# documented in docs/hermes_tool_bridge_runbook.md §2.
#
# Run from the project root:
#   cd C:\Trading\ai_fibo_vision_trader
#   .\scripts\hermes_tool_bridge_smoke.ps1
#
# Safety:
#   * Pre-flight refuses to run unless LIVE_TRADING / EXECUTION_MODE /
#     BROKER_MODE are the mock-only values.
#   * Does NOT pass --yolo or --accept-hooks to hermes.
#   * Uses `-z` (one-shot) which auto-bypasses *interactive
#     approvals* so the pipe does not hang -- this is NOT the same as
#     --yolo. The prompt itself is scoped to reading AGENTS.md from
#     the project root.
#   * Touches no broker SDK, no broker credential, no TradingView
#     session, no live order path.
#   * Launches Hermes via its venv Python entrypoint
#     (`hermes_cli.main`), NOT via `hermes.exe`, because Windows
#     AppLocker / WDAC can silently block the .exe. Same approach as
#     scripts/start_hermes.ps1 (see docs/hermes_operating_runbook.md
#     section 7).
# ============================================================

$ErrorActionPreference = "Stop"

# -------- 0. Pre-flight --------
foreach ($p in @(
    @{ k = "LIVE_TRADING";   want = "false" },
    @{ k = "EXECUTION_MODE"; want = "mock"  },
    @{ k = "BROKER_MODE";    want = "mock"  }
)) {
    $got = [Environment]::GetEnvironmentVariable($p.k, "Process")
    if ($got -and $got -ne $p.want) {
        Write-Host "ABORT: $($p.k)=$got (must be '$($p.want)')" -ForegroundColor Red
        exit 2
    }
}
Write-Host "[OK] mock-only envelope clean"

# Resolve Hermes entrypoint via the venv Python (NOT hermes.exe).
# Windows AppLocker / WDAC can silently block the .exe even when on PATH;
# scripts/start_hermes.ps1 sets $env:HERMES_PY for the same reason.
$hermesPy = $env:HERMES_PY
if (-not $hermesPy) {
    $hermesPy = Join-Path $env:LOCALAPPDATA "hermes\hermes-agent\venv\Scripts\python.exe"
}
if (-not (Test-Path $hermesPy)) {
    Write-Host "ABORT: Hermes venv python not found: $hermesPy" -ForegroundColor Red
    Write-Host "       Run .\scripts\start_hermes.ps1 in another shell first to verify the install," -ForegroundColor Red
    Write-Host "       or set `$env:HERMES_PY to the correct venv python.exe path." -ForegroundColor Red
    exit 2
}
Write-Host "[OK] hermes venv python: $hermesPy"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

if (-not (Test-Path (Join-Path $projectRoot "AGENTS.md"))) {
    Write-Host "ABORT: AGENTS.md not in $projectRoot -- the prompt assumes it exists" -ForegroundColor Red
    exit 2
}
Write-Host "[OK] AGENTS.md present in $projectRoot"

# Read the *expected* first heading from AGENTS.md so we can check
# Hermes' reply against ground truth.
$expectedFirstHeading = (Get-Content AGENTS.md -Encoding UTF8 |
    Where-Object { $_ -match '^\s*#\s+\S' } |
    Select-Object -First 1).Trim()
if (-not $expectedFirstHeading) {
    Write-Host "ABORT: could not find a first heading in AGENTS.md" -ForegroundColor Red
    exit 2
}
Write-Host "[OK] expected first heading: '$expectedFirstHeading'"
Write-Host ""

# -------- 1. Send the smoke prompt --------
$prompt = @'
Read the file AGENTS.md from the current working directory using your
file-read tool. After the tool returns, reply with ONLY the first
markdown heading line (the first line that begins with "# ") and
nothing else. Do NOT emit any JSON, do NOT write the tool call payload
in your response body, do NOT wrap your answer in code fences.
'@

Write-Host "========== sending smoke prompt to hermes (-z, via venv python) =========="
Write-Host $prompt
Write-Host "========================================================================="
Write-Host ""

# Call hermes via Start-Job + call operator (&) so PowerShell's modern
# parameter binder handles arg quoting. The previous Start-Process
# -ArgumentList path silently dropped quotes around `$entrypoint`, so
# `python.exe -c <multi-token>` only saw the first token (`from`) and
# died with `SyntaxError: invalid syntax`. See proposal "Option A".
$tmpOut = New-TemporaryFile
$tmpErr = New-TemporaryFile
$entrypoint = "from hermes_cli.main import main; raise SystemExit(main())"
$job = Start-Job -ScriptBlock {
    param($py, $ep, $promptArg, $outFile, $errFile)
    & $py -c $ep -z $promptArg 1>$outFile 2>$errFile
    $LASTEXITCODE
} -ArgumentList $hermesPy, $entrypoint, $prompt, $tmpOut.FullName, $tmpErr.FullName

if (-not (Wait-Job $job -Timeout 120)) {
    Stop-Job $job
    Remove-Job $job -Force
    Write-Host "FAIL: hermes -z timed out after 120 s" -ForegroundColor Red
    Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
    exit 3
}
$exitCode = Receive-Job $job
Remove-Job $job
$reply  = (Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue)
$stderr = (Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue)
Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue

if ($exitCode -ne 0) {
    Write-Host "FAIL: hermes -z exit $exitCode" -ForegroundColor Red
    if ($stderr) { Write-Host "stderr:`n$stderr" -ForegroundColor Red }
    exit 3
}

Write-Host "----- hermes reply (raw) -----"
Write-Host $reply
Write-Host "------------------------------"
Write-Host ""

# -------- 2. Heuristics --------
$jsonLeakPatterns = @(
    '"tool_calls"',
    '"function"\s*:',
    '<tool_call>',
    '<\|tool_call\|>',
    'read_file\s*\(\s*\{',
    'read_file\(\s*"path"',
    '"name"\s*:\s*"read_file"'
)
$noToolPatterns = @(
    'cannot\s+call',
    'no\s+tool\s+available',
    'I\s+do\s+not\s+have\s+(a\s+)?tool',
    'tool\s+is\s+not\s+available',
    'I\s+am\s+unable\s+to\s+(read|call)'
)

$replyLower = ($reply -as [string])

$jsonHit = $false
foreach ($pat in $jsonLeakPatterns) {
    if ($replyLower -match $pat) {
        $jsonHit = $true
        Write-Host "FAIL (json-as-text): reply contains pattern '$pat'" -ForegroundColor Red
        break
    }
}
if ($jsonHit) {
    Write-Host "  -> Tool channel is broken; the model wrote the tool call as text."
    Write-Host "  -> Apply workaround A from docs/hermes_tool_bridge_runbook.md §3,"
    Write-Host "     re-run this smoke. If still failing, escalate to B then C."
    exit 4
}

$noToolHit = $false
foreach ($pat in $noToolPatterns) {
    if ($replyLower -match $pat) {
        $noToolHit = $true
        Write-Host "FAIL (no tool surfaced): reply says it cannot call a tool" -ForegroundColor Red
        Write-Host "  matched pattern: '$pat'"
        break
    }
}
if ($noToolHit) {
    Write-Host "  -> Hermes' file tool was not registered for this session."
    Write-Host "  -> Check `hermes doctor` Tool Availability section."
    exit 4
}

if ($reply -match [regex]::Escape($expectedFirstHeading)) {
    Write-Host "PASS: reply contains the real first heading of AGENTS.md" -ForegroundColor Green
    Write-Host "  expected: $expectedFirstHeading"
    exit 0
}

Write-Host "FAIL (other): reply did not match the expected first heading" -ForegroundColor Red
Write-Host "  expected: $expectedFirstHeading"
Write-Host "  reply (first 200 chars): $($reply.Substring(0, [Math]::Min(200, $reply.Length)))"
exit 4
