# ============================================================
# AI Fibo Vision Trader - Windows 11 setup
# Run from an elevated PowerShell:
#   cd C:\Trading\ai_fibo_vision_trader
#   powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
# ============================================================

$ErrorActionPreference = "Stop"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Error "Python launcher 'py' not found. Install Python 3.11 from python.org first."
}

Write-Host "[1/4] Creating .venv (Python 3.11)..."
py -3.11 -m venv .venv

Write-Host "[2/4] Activating venv..."
. .\.venv\Scripts\Activate.ps1

Write-Host "[3/4] Upgrading pip..."
python -m pip install --upgrade pip

Write-Host "[4/4] Installing requirements..."
# Install CUDA 12.1 build of torch first (matches RTX 3090 driver stack).
pip install --index-url https://download.pytorch.org/whl/cu121 torch
pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from template."
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Start the API with:   .\scripts\run.ps1"
Write-Host "Run tests with:       .\scripts\test.ps1"
