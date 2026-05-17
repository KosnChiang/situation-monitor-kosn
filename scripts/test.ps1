# Run pytest under the project venv.
$ErrorActionPreference = "Stop"

. .\.venv\Scripts\Activate.ps1

$env:LIVE_TRADING = "false"
$env:EXECUTION_MODE = "mock"

python -m pytest -v
