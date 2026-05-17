# AI Fibo Vision Trader (Mock Build)

Visual auto-trading bot that watches your chart screen, detects Fibonacci
levels with a YOLO model, runs the **Fibo MOB v2** strategy, and emits
**simulated** trades only. **No real orders are placed.**

> Safety: this build is hard-wired to mock mode.
> `LIVE_TRADING=false` and `EXECUTION_MODE=mock` are enforced at three
> layers (env loader, `RiskGate`, `MockExecutor`). Any attempt to flip
> them will refuse to start, and a unit test scans the source tree for
> broker SDK imports and live-order function names.

---

## Target environment

- **Windows 11**
- **Python 3.11**
- **Dual NVIDIA RTX 3090** — inference pinned to GPU #1 via
  `CUDA_VISIBLE_DEVICES=1`.
- Project root: `C:\Trading\ai_fibo_vision_trader`

---

## Setup (one-time)

Open **PowerShell** and run:

```powershell
# Clone into the canonical project path
git clone <this-repo> C:\Trading\ai_fibo_vision_trader
cd C:\Trading\ai_fibo_vision_trader

# Build venv + install everything (torch is pulled from the CUDA 12.1 channel)
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1
```

What the script does:

1. `py -3.11 -m venv .venv`
2. activates `.venv`
3. `pip install --index-url https://download.pytorch.org/whl/cu121 torch`
4. `pip install -r requirements.txt`
5. copies `.env.example` to `.env` if missing

Dependencies installed: `torch`, `ultralytics`, `opencv-python`, `mss`,
`numpy`, `pandas`, `fastapi`, `uvicorn`, `python-dotenv`, `requests`,
plus `pytest` and `httpx` for tests.

---

## Run

```powershell
.\scripts\run.ps1
```

This sets `CUDA_VISIBLE_DEVICES=1` and starts uvicorn on
`http://127.0.0.1:8765`.

### Health check

```
GET http://127.0.0.1:8765/health
```

Example response:

```json
{
  "status": "ok",
  "live_trading": "false",
  "execution_mode": "mock",
  "cuda_visible_devices": "1",
  "signals_today": 0,
  "telegram_enabled": false
}
```

### Webhook signal injection

```
POST http://127.0.0.1:8765/signal
Content-Type: application/json

{
  "side": "LONG",
  "entry": 23010.5,
  "stop":  22980.0,
  "target": 23090.0,
  "confidence": 0.78,
  "reason": "tradingview-alert"
}
```

Approved signals are written to `logs/trades.jsonl` (one JSON object per
line). Nothing leaves the machine except an optional Telegram message,
and only if `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` are configured.

---

## Tests

```powershell
.\scripts\test.ps1
```

The test suite verifies that:

- no broker SDKs (`ib_insync`, `ibapi`, `alpaca`, `ccxt`, `binance`,
  `oandapyV20`, `MetaTrader5`, `shioaji`) are imported anywhere;
- no function named `place_order`, `submit_order`, `send_order`,
  `live_order`, `real_order`, etc. is defined;
- `RiskGate` refuses to construct when `LIVE_TRADING=true` or
  `EXECUTION_MODE!=mock`;
- `MockExecutor.submit` refuses when env flags are flipped at runtime;
- `MockExecutor` appends a valid JSON line to `logs/trades.jsonl`;
- the FastAPI `/health` and `/signal` endpoints behave correctly.

---

## Module map

| Module                          | Purpose                                                   |
| ------------------------------- | --------------------------------------------------------- |
| `capture/screen_capture.py`     | mss-based screen capture, BGR numpy frames                |
| `vision/fibo_detector.py`       | Ultralytics YOLO detector for Fibo levels                 |
| `strategy/fibo_mob_v2.py`       | Fibo MOB v2 — pure decision function                      |
| `risk/risk_gate.py`             | Mock-mode invariant + confidence & daily-cap filters       |
| `executor/mock_executor.py`     | Appends fills to `logs/trades.jsonl` (no network)         |
| `notify/telegram_bot.py`        | Optional Telegram push, disabled when creds blank         |
| `app/main.py`                   | FastAPI app: `GET /health`, `POST /signal`                |

---

## Windows 11 + dual RTX 3090 startup flow

This is the exact order to run on the trading box. It pins **all**
inference to GPU #1 so the desktop and any TradingView windows stay on
GPU #0.

```powershell
# 0) Open PowerShell in the project root
cd C:\Trading\ai_fibo_vision_trader

# 1) First-time setup (venv + torch CUDA 12.1 + deps + .env)
powershell -ExecutionPolicy Bypass -File .\scripts\setup_windows.ps1

# 2) Confirm both 3090s are seen and that GPU #1 is the one we pin to
$env:CUDA_VISIBLE_DEVICES = "1"
. .\.venv\Scripts\Activate.ps1
python -m tools.gpu_check
# Expected: cuda_available=true, device_count=1, devices[0].name contains "3090"
# (count is 1 because CUDA_VISIBLE_DEVICES=1 hides card #0 from this process)

# 3) Drop CUDA_VISIBLE_DEVICES temporarily to verify the host actually has TWO 3090s
Remove-Item Env:CUDA_VISIBLE_DEVICES
python -m tools.gpu_check
# Expected: device_count=2, both devices named "NVIDIA GeForce RTX 3090"

# 4) Edit config\capture.yaml so monitor_index / left / top / width / height
#    cover the TradingView chart pane on the secondary monitor.

# 5) Smoke-test the capture region — writes logs\capture_test.png
$env:CUDA_VISIBLE_DEVICES = "1"
python -m tools.capture_test
# Open logs\capture_test.png and confirm it shows the chart area you want.

# 6) Run the mock-mode guard tests
.\scripts\test.ps1

# 7) Start the API (uses CUDA_VISIBLE_DEVICES=1 internally)
.\scripts\run.ps1
# Then in another terminal:
#   curl http://127.0.0.1:8765/health
```

> Why card #1? On a typical dual-3090 build the monitors are plugged
> into card #0 and the Windows desktop compositor runs there. Pinning
> CUDA workloads to card #1 keeps the YOLO detector off the display
> path, so chart redraws and screen capture do not stall while
> inference runs.

---

## Going live (NOT enabled here)

To add real trading later you would:

1. write a new `executor/<broker>_executor.py` that implements `submit`;
2. expand `tests/test_mock_only.py` to **allow** that broker's SDK;
3. remove the hard refusal in `risk/risk_gate.py`.

None of those steps are done in this repo — by design.
