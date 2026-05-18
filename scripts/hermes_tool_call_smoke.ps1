# ============================================================
# scripts/hermes_tool_call_smoke.ps1
#
# Minimal operator-driven check that Hermes is actually invoking
# its terminal / read_file tool, not hallucinating an OpenAI-style
# tool-call JSON envelope and printing it as text. See
# docs/hermes_operating_runbook.md  7.5 for the failure mode.
#
# This script does NOT launch Hermes and does NOT submit any order.
# It is a wizard:
#   1. Prints a canonical prompt for the operator to paste into Hermes.
#   2. Reads the operator's pasted Hermes reply from stdin (multi-line,
#      finished with a blank line).
#   3. Reports PASS if the reply contains real file content and FAIL if
#      it contains a fake tool-call envelope.
#
# Exit codes:
#   0  PASS  -- real content, no fake tool JSON
#   1  FAIL  -- empty / wrong content / fake tool-call JSON detected
# ============================================================

param(
    [string]$ExpectedFile = "docs/hermes_training_profile.md",
    [string]$ExpectedSubstring = "Hermes Project Training Profile"
)

$ErrorActionPreference = "Continue"

$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

$expectedPath = Join-Path $projectRoot $ExpectedFile
if (-not (Test-Path $expectedPath)) {
    Write-Host "[FAIL] expected file does not exist: $expectedPath"
    Write-Host "       (this smoke wizard checks Hermes can read it; the file must be present)"
    exit 1
}

# Build the operator-facing prompt. We deliberately spell out: use a real
# tool, do NOT print JSON. This is the same constraint encoded in
# prompts/hermes_project_system_prompt.md  Tool calling.
$prompt = @"
Please run the equivalent of:
    Get-Content .\$ExpectedFile -TotalCount 5
using your real terminal or read_file tool, and paste back the first 5
non-empty lines of that file verbatim.

Do NOT print a JSON tool-call envelope as text (no { "name": "terminal",
"arguments": { ... } } and no <tool_call> tags). If a tool is not
available in this session, reply in plain prose explaining that, and
suggest the PowerShell command for me to run myself.
"@

Write-Host "===================================================================="
Write-Host "Hermes tool-call smoke test (operator-driven; read-only)"
Write-Host "===================================================================="
Write-Host ""
Write-Host "Target file       : $ExpectedFile"
Write-Host "Expected substring: '$ExpectedSubstring'"
Write-Host ""
Write-Host "Step 1. In your Hermes session (another window), paste this prompt:"
Write-Host ""
Write-Host "----- BEGIN PROMPT -----"
Write-Host $prompt
Write-Host "----- END PROMPT -----"
Write-Host ""
Write-Host "Step 2. Wait for Hermes to reply."
Write-Host "Step 3. Paste the FULL Hermes reply below."
Write-Host "        Finish input with an empty line."
Write-Host ""

# Read multi-line input until a blank line.
$lines = @()
while ($true) {
    $l = Read-Host
    if ([string]::IsNullOrEmpty($l)) { break }
    $lines += $l
}
$reply = $lines -join "`n"

if (-not $reply) {
    Write-Host ""
    Write-Host "[FAIL] empty reply; nothing to evaluate."
    exit 1
}

# Detect the fake tool-call shapes. These patterns intentionally match the
# residue from other agent stacks (OpenAI Functions / Qwen native /
# Anthropic tool_use) -- Hermes will NOT execute any of them when emitted
# as assistant text.
$jsonShape = '"name"\s*:\s*"(terminal|read_file|run_command|execute_code|shell|bash|powershell)"|"arguments"\s*:|"function"\s*:|"parameters"\s*:'
$tagShape  = '<tool_call>|</tool_call>|<function_call>|</function_call>'

$badJson = [bool]([regex]::IsMatch($reply, $jsonShape))
$badTag  = [bool]([regex]::IsMatch($reply, $tagShape))
$good    = $reply.Contains($ExpectedSubstring)

Write-Host ""
Write-Host "----- evaluation -----"
Write-Host ("contains fake tool-call JSON : {0}" -f $badJson)
Write-Host ("contains <tool_call> tag     : {0}" -f $badTag)
Write-Host ("contains expected substring  : {0}" -f $good)
Write-Host ""

if ($badJson -or $badTag) {
    Write-Host "[FAIL] reply contains a tool-call envelope as text."
    Write-Host "       Hermes did NOT execute a real tool."
    Write-Host "       See docs/hermes_operating_runbook.md  7.5 for fixes:"
    Write-Host "         1. Upgrade `$env:LOCALAPPDATA\hermes\config.yaml model.default"
    Write-Host "            to qwen2.5-coder:14b-64k (or larger)."
    Write-Host "         2. Re-paste prompts/hermes_project_system_prompt.md into"
    Write-Host "            Hermes' /system slot."
    Write-Host "         3. Re-run this script."
    exit 1
}

if (-not $good) {
    Write-Host "[FAIL] reply did NOT contain expected substring '$ExpectedSubstring'."
    Write-Host "       Either the target file changed (update -ExpectedSubstring),"
    Write-Host "       or Hermes did not actually read it."
    exit 1
}

Write-Host "[PASS] reply contains real file content; no fake tool-call envelope."
exit 0
