# Start the mock trading API. Pins inference to GPU #1 (CUDA_VISIBLE_DEVICES=1).
$ErrorActionPreference = "Stop"

. .\.venv\Scripts\Activate.ps1

$env:CUDA_VISIBLE_DEVICES = "1"
$env:LIVE_TRADING = "false"
$env:EXECUTION_MODE = "mock"

python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
