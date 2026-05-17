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

## Hermes integration (safety scaffold only — no installer)

**Target Hermes**: [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
The official installer is one PowerShell line — this repo only ships
the safety envelope that runs around it: a hard-mock launcher, a path
allowlist (project convention, see caveat below), and 11 guard tests.

### Honest gaps between your spec and what Hermes Agent documents

Before installing, read these. The scaffold is written around them, not
in denial of them:

1. **Install path.** Hermes Agent installs to `%LOCALAPPDATA%\hermes`,
   not `C:\Trading\hermes`. The official installer does not take a
   path flag. `scripts\start_hermes.ps1` therefore resolves the
   `hermes` binary from `PATH` after install. If you want it physically
   under `C:\Trading\hermes`, install first and then move/symlink
   manually — but accept that future `hermes` self-updates may surprise
   you.
2. **Ollama.** Hermes Agent's documented providers are OpenRouter,
   OpenAI, and "others"; the README does **not** mention Ollama or
   `OLLAMA_BASE_URL` / `OLLAMA_MODEL`. The launcher still exports those
   env vars in case a future Hermes provider picks them up, but you
   should verify with `hermes model` and `hermes setup` whether
   `qwen2.5:14b` via Ollama is actually selectable. If not, the usual
   workaround is to put an OpenAI-compatible shim in front of Ollama
   (Ollama already exposes `/v1` for this) and configure Hermes to talk
   to that.
3. **Path allowlist.** Hermes Agent has *interactive command approval*
   and *container isolation*, but no documented YAML allowlist.
   `configs/hermes_allowlist.yaml` in this repo is **project
   convention**, not an OS sandbox. Hard restriction to four directories
   needs OS-level enforcement on Windows (a restricted Windows user
   account whose only writable folders are the four roots, plus
   Hermes' own command-approval prompts).

### What this scaffold gives you

### What this scaffold gives you

| File                                | Purpose                                                                 |
| ----------------------------------- | ----------------------------------------------------------------------- |
| `scripts/start_hermes.ps1`          | Pins `LIVE_TRADING=false`, `EXECUTION_MODE=mock`, `BROKER_MODE=mock`, `CUDA_VISIBLE_DEVICES=1`, refuses to flip them, refuses to launch until `HERMES_EXEC` is set, and refuses if any broker credential env (`SHIOAJI_API_KEY`, `IB_PASSWORD`, `MT5_LOGIN`, `BINANCE_API_KEY`, `ALPACA_API_KEY`, `CTPRO_USER` …) is in scope. |
| `configs/hermes_allowlist.yaml`     | Whitelists exactly four paths: `C:\Trading\ai_fibo_vision_trader`, `C:\Trading\configs`, `C:\Trading\logs`, `D:\TradingData`. Denies `.env`, credentials, secrets, CTPro, broker SDK paths. Denies broker SDK commands. |
| `configs/hermes.env.example`        | Env overlay template. `HERMES_EXEC=` is intentionally **blank**.        |
| `tests/test_hermes_safety.py`       | Eleven tests that fail loudly if any of the above safety properties regress. |

### Windows setup (after you decide which Hermes to use)

```powershell
# 1) Pull the latest scaffold
cd C:\Trading\ai_fibo_vision_trader
git pull

# 2) Make the shared config directory and copy the scaffold there
New-Item -ItemType Directory -Force -Path C:\Trading\configs | Out-Null
New-Item -ItemType Directory -Force -Path C:\Trading\logs   | Out-Null
New-Item -ItemType Directory -Force -Path D:\TradingData    | Out-Null
Copy-Item .\configs\hermes_allowlist.yaml C:\Trading\configs\hermes_allowlist.yaml
Copy-Item .\configs\hermes.env.example    C:\Trading\configs\hermes.env

# 3) Install Hermes Agent (official one-liner; installs to %LOCALAPPDATA%\hermes
#    and puts `hermes` on PATH).
irm https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.ps1 | iex

# 4) Re-open PowerShell so PATH is refreshed, then verify:
hermes --version

# 5) Leave HERMES_EXEC blank in C:\Trading\configs\hermes.env -- the
#    launcher resolves `hermes` from PATH. Only set it if you have
#    intentionally moved the binary to a custom location. Do NOT edit
#    LIVE_TRADING / EXECUTION_MODE / BROKER_MODE; the launcher will
#    refuse to honour overrides anyway.

# 6) Pull the Ollama model you intend to use (assuming Hermes can talk
#    to Ollama -- see gap #2 above; verify with `hermes model`).
ollama pull qwen2.5:14b

# 7) Run `hermes setup` and configure the provider. If Hermes does not
#    list Ollama as a provider, point it at an OpenAI-compatible base
#    URL of http://127.0.0.1:11434/v1 (Ollama's OpenAI-compatible
#    endpoint) and set the model name to qwen2.5:14b.
hermes setup

# 8) Run the safety tests BEFORE launching
.\scripts\test.ps1

# 9) Launch Hermes through the safe wrapper
.\scripts\start_hermes.ps1
```

### Safety properties enforced

* Mock mode is set by the launcher and **cannot** be flipped by
  `hermes.env` — those keys are explicitly stripped on read.
* `HERMES_EXEC` blank ⇒ launcher prints the configuration and exits 0
  without invoking anything. There is no "default" Hermes path.
* No file in this scaffold imports or references a broker SDK
  (`shioaji`, `ib_insync`, `ibapi`, `MetaTrader5`, `ccxt`, `binance`,
  `alpaca`, `oandapyV20`) outside an explicit denial list.
* No file references CTPro credentials or any broker token/password
  variable as an *assignment*.
* The allowlist is exactly the four approved roots — adding a fifth
  fails `test_allowlist_contains_exactly_the_four_roots`.

### What I did NOT do

* Did **not** install Hermes — source unconfirmed.
* Did **not** create `C:\Trading\hermes` — that's your manual step
  once you've verified which Hermes distribution to use.
* Did **not** add any broker SDK to `requirements.txt`.
* Did **not** read, write, or reference any broker credential.

---

## Going live (NOT enabled here)

To add real trading later you would:

1. write a new `executor/<broker>_executor.py` that implements `submit`;
2. expand `tests/test_mock_only.py` to **allow** that broker's SDK;
3. remove the hard refusal in `risk/risk_gate.py`.

None of those steps are done in this repo — by design.
