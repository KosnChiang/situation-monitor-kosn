# ============================================================
# scripts/test.ps1 -- run the project's pytest suite scoped strictly
# to .\tests under the mock-only envelope.
#
# Failure mode this fixes: on the Windows trading box a sibling clone
# of hermes-webui inside the project root caused pytest's default
# auto-discovery (`pytest -v` with no args) to walk into
# hermes-webui\tests\, ballooning the run to 5549 tests / 34
# collection errors. This script keeps the suite scope intentional.
#
# Defenses, in layered order:
#   1. pytest.ini at the repo root pins testpaths = tests and lists
#      foreign trees in norecursedirs. That handles bare `pytest` too.
#   2. This script explicitly passes `.\tests` as the testpath plus
#      --ignore flags for the five common foreign-tree names, so even
#      if someone deletes pytest.ini the scope holds.
#   3. PYTHONPATH is pinned to the repo root so production modules
#      resolve without an editable install.
#   4. PYTHONUTF8=1 forces Python's IO encoding to UTF-8 on Windows
#      hosts so the Chinese denial markers in tests/test_no_hermes_yolo.py
#      and friends load cleanly on cp950 / cp1252 consoles.
#   5. LIVE_TRADING / EXECUTION_MODE / BROKER_MODE are pinned to the
#      mock-only values; the script refuses to run if the caller has
#      already set any of them to something else.
#
# Forwarding: any extra args you pass are appended after the explicit
# pytest invocation, e.g. `.\scripts\test.ps1 -k fibo -v`.
# ============================================================

$ErrorActionPreference = "Stop"

# Locate repo root regardless of cwd. When invoked via
# `powershell -File .\scripts\test.ps1`, $PSScriptRoot is .\scripts.
$projectRoot = if ($PSScriptRoot) { Split-Path $PSScriptRoot -Parent } else { (Get-Location).Path }
Set-Location $projectRoot

# Refuse to run if a non-mock envelope is already in scope. This is
# defense-in-depth against an operator who exported LIVE_TRADING=true
# earlier in the session and then ran the test suite expecting it to
# still pass; the suite must never confirm safety under live flags.
if ($env:LIVE_TRADING -and $env:LIVE_TRADING -ne "false") {
    Write-Error "Refusing to run: LIVE_TRADING=$($env:LIVE_TRADING). Must be 'false'."
}
if ($env:EXECUTION_MODE -and $env:EXECUTION_MODE -ne "mock") {
    Write-Error "Refusing to run: EXECUTION_MODE=$($env:EXECUTION_MODE). Must be 'mock'."
}
if ($env:BROKER_MODE -and $env:BROKER_MODE -ne "mock") {
    Write-Error "Refusing to run: BROKER_MODE=$($env:BROKER_MODE). Must be 'mock'."
}

# Optional venv activation (sets VIRTUAL_ENV for packages that look
# for it). We still call the venv python.exe explicitly below so the
# correct interpreter is used even if Activate.ps1 is unavailable
# (e.g. execution policy issues).
$venvActivate = Join-Path $projectRoot ".venv\Scripts\Activate.ps1"
$venvPython   = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvActivate) {
    . $venvActivate
}
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

# Pin the mock-mode envelope and Python interpreter settings.
$env:LIVE_TRADING   = "false"
$env:EXECUTION_MODE = "mock"
$env:BROKER_MODE    = "mock"
$env:PYTHONPATH     = $projectRoot
$env:PYTHONUTF8     = "1"

Write-Host "test.ps1 -- mock-only invocation"
Write-Host "  projectRoot      : $projectRoot"
Write-Host "  python           : $python"
Write-Host "  PYTHONPATH       : $env:PYTHONPATH"
Write-Host "  PYTHONUTF8       : $env:PYTHONUTF8"
Write-Host "  LIVE_TRADING     : $env:LIVE_TRADING"
Write-Host "  EXECUTION_MODE   : $env:EXECUTION_MODE"
Write-Host "  BROKER_MODE      : $env:BROKER_MODE"
Write-Host ""

& $python -m pytest .\tests `
    --ignore=hermes-webui `
    --ignore=external `
    --ignore=vendor `
    --ignore=third_party `
    --ignore=node_modules `
    --ignore=.venv `
    -q @args

exit $LASTEXITCODE
