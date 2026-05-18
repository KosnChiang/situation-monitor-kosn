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
#   * Uses `hermes -z` (one-shot) which auto-bypasses *interactive
#     approvals* so the pipe does not hang -- this is NOT the same as
#     --yolo. The prompt itself is scoped to reading AGENTS.md from
#     the project root.
#   * Touches no broker SDK, no broker credential, no TradingView
#     session, no live order path.
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

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Write-Host "ABORT: hermes not on PATH" -ForegroundColor Red
    exit 2
}
Write-Host "[OK] hermes on PATH"

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

Write-Host "========== sending smoke prompt to hermes -z =========="
Write-Host $prompt
Write-Host "========================================================"
Write-Host ""

$tmpOut = New-TemporaryFile
$tmpErr = New-TemporaryFile
$p = Start-Process -FilePath (Get-Command hermes).Source `
    -ArgumentList @("-z", $prompt) `
    -NoNewWindow -PassThru `
    -RedirectStandardOutput $tmpOut `
    -RedirectStandardError  $tmpErr
if (-not $p.WaitForExit(120 * 1000)) {
    $p.Kill()
    Write-Host "FAIL: hermes -z timed out after 120 s" -ForegroundColor Red
    Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
    exit 3
}
$reply  = (Get-Content $tmpOut -Raw -ErrorAction SilentlyContinue)
$stderr = (Get-Content $tmpErr -Raw -ErrorAction SilentlyContinue)
Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue

if ($p.ExitCode -ne 0) {
    Write-Host "FAIL: hermes -z exit $($p.ExitCode)" -ForegroundColor Red
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
