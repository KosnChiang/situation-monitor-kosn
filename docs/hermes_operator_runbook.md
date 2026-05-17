# Hermes Operator Runbook

> Operator-side procedures for running Hermes Agent on the Windows
> trading box against the AI Fibo Vision Trader project, **mock-only**.
> Last updated: Phase 4 handoff.

This runbook covers day-to-day operation. The architecture and Phase-1
mock-only guarantees are documented in the project README.

---

## 1. 啟動 Hermes

Always launch through the project's wrapper, never bare `hermes`:

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\start_hermes.ps1
```

The wrapper does **all** of the following before Hermes ever loads:

1. Pins `LIVE_TRADING=false`, `EXECUTION_MODE=mock`, `BROKER_MODE=mock`.
2. Pins `CUDA_VISIBLE_DEVICES=1` (LLM stays on the second 3090; the
   first 3090 is reserved for vision).
3. Sets `OLLAMA_BASE_URL=http://127.0.0.1:11434` and an Ollama main
   model default.
4. Loads the path allowlist from
   `C:\Trading\configs\hermes_allowlist.yaml` (refuses to start if
   missing).
5. Reads `C:\Trading\configs\hermes.env` if present, but **silently
   discards** any line that tries to flip `LIVE_TRADING`,
   `EXECUTION_MODE`, or `BROKER_MODE`.
6. Re-checks all three envelope env vars and aborts if anything is
   not the expected value.
7. Aborts if any broker-credential env name
   (`SHIOAJI_API_KEY`, `IB_PASSWORD`, `MT5_LOGIN`,
   `BINANCE_API_KEY`, `ALPACA_API_KEY`, `CTPRO_USER`, …) is set in
   the current process scope.
8. Resolves `HERMES_EXEC` (explicit env override → `hermes` on PATH →
   refuse to launch).

Never pass `--yolo` or `--accept-hooks`. Never set
`HERMES_ACCEPT_HOOKS=1`. These auto-approve dangerous shell hooks and
defeat the allowlist.

---

## 2. 確認 Hermes 使用本機 Ollama

After Hermes starts, the bottom status bar shows the active model.
Acceptable values for this build:

* main model: anything served by your local Ollama at
  `http://127.0.0.1:11434/v1` — usually `qwen2.5:14b` or `qwen2.5:32b`
  (or a custom-tagged variant);
* auxiliary compression model: `qwen2.5-32b-instruct-q4_K_M-64k`
  (built locally with `num_ctx=65536` — see Phase-2 README and
  `scripts/fix_hermes_64k.ps1`).

To inspect or switch:

```powershell
# View current configuration:
hermes config | Select-String -Pattern "provider|model|inference|compression" -Context 0,2

# Interactive picker for the main inference model:
hermes model
```

If you ever see `$ unknown` in the status bar, or
`No inference provider configured` when sending a message, the user
`config.yaml` was rejected during load. See **§ 8 Recovery** below.

---

## 3. 確認 mock-only

Three independent layers must all read mock. From a separate
PowerShell tab:

```powershell
# Layer 1 -- the env vars the wrapper just pinned:
$env:LIVE_TRADING; $env:EXECUTION_MODE; $env:BROKER_MODE
# expected: false / mock / mock

# Layer 2 -- the Python RiskGate refuses anything else at construction:
cd C:\Trading\ai_fibo_vision_trader
.\.venv\Scripts\python.exe -c "from risk.risk_gate import RiskGate; RiskGate()"
# expected: no exception. Flip any env var and rerun -- it WILL raise.

# Layer 3 -- the MockExecutor refuses to write live orders:
.\.venv\Scripts\python.exe -c "from executor.mock_executor import MockExecutor; print(MockExecutor)"
```

If any layer raises or prints anything other than success, **stop
trading work** and investigate before continuing.

---

## 4. 跑測試

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\test.ps1
```

Expected: at least **33 passed** at Phase-3 baseline; **37 passed**
after Phase-4 (this runbook + the additional repo-wide guard tests).

Any failure in `tests/test_mock_only.py`,
`tests/test_hermes_safety.py`, or `tests/test_no_hermes_yolo.py` is a
**hard stop**. These are the safety tests.

---

## 5. 跑 capture_test

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\.venv\Scripts\python.exe -m tools.capture_test
```

This captures a single frame from the configured screen-capture
source and writes it under `D:\TradingData\capture\`. It does **not**
load any vision model, does **not** generate a trade signal, and is
safe to run repeatedly.

Tail the most recent capture:

```powershell
Get-ChildItem D:\TradingData\capture\ -Recurse -Filter *.png |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
```

---

## 6. 停用 Hermes

```powershell
# In the Hermes terminal:  Ctrl+C  (graceful)
# or close the window      (hard)
```

To revert the compression-model swap (if Hermes started misbehaving
after a config patch):

```powershell
$cfg = "$env:LOCALAPPDATA\hermes\config.yaml"
$bak = Get-ChildItem "$cfg.bak.*" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($bak) {
    Copy-Item $bak.FullName $cfg -Force
    Write-Host "Restored from $($bak.FullName)"
} else {
    Write-Host "No .bak file found next to $cfg"
}
```

To stop and disable the Ollama service (so no LLM is loaded at all):

```powershell
Get-Process ollama -ErrorAction SilentlyContinue | Stop-Process -Force
```

---

## 7. 禁止事項

These are **permanently forbidden** on this trading box. CI and
runtime checks enforce them; violations are bugs, not preferences.

| # | Forbidden | Why | Enforced by |
|---|-----------|-----|-------------|
| 1 | Connecting to CTPro for real trades | live-order surface | `tests/test_mock_only.py`, allowlist deny list |
| 2 | Installing any broker SDK: `shioaji`, `ib_insync`, `ibapi`, `MetaTrader5`, `ccxt`, `binance`, `alpaca`, `oandapyV20` | live-order surface | `tests/test_mock_only.py::test_no_forbidden_broker_imports`, `requirements.txt` review |
| 3 | Reading or writing any broker credential env name | secret-exfiltration surface | `start_hermes.ps1` refuses if any are set; `hermes_allowlist.yaml` denies `.env`, credentials, secrets |
| 4 | Setting `LIVE_TRADING=true` (or `1`/`yes`/`on`) anywhere | execution-mode bypass | `tests/test_no_hermes_yolo.py`, `RiskGate.__init__` refuses |
| 5 | Setting `EXECUTION_MODE` to anything other than `mock` | execution-mode bypass | `start_hermes.ps1` refuses, `RiskGate` refuses |
| 6 | Setting `BROKER_MODE` to anything other than `mock` | broker-binding bypass | `start_hermes.ps1` refuses, `RiskGate` refuses |
| 7 | Passing `--yolo` to `hermes` | bypasses **all** dangerous-command approval | `tests/test_no_hermes_yolo.py` |
| 8 | Passing `--accept-hooks` to `hermes` | auto-approves arbitrary shell hooks declared in config.yaml | `tests/test_no_hermes_yolo.py` |
| 9 | Setting `HERMES_ACCEPT_HOOKS=1` (or any truthy) | same as `--accept-hooks` but via env | `tests/test_no_hermes_yolo.py` |

If you ever genuinely need to lift one of these (e.g. to go live with
a different scaffold), do it on a **separate branch** and a **separate
machine**. Do not weaken any of these guards on the mock-only build.

---

## 8. Recovery: Hermes ignored my config.yaml

Symptom: at Hermes startup banner you see a yellow/orange line such
as

> `... position N. Falling back to default config -- every user
> override (auxiliary providers, fallback chain, model settings) is
> being IGNORED.`

…and `hi` answers `No inference provider configured`. This means
Hermes refused to parse `config.yaml` and is running on built-in
defaults, so every override (provider, fallback chain, compression
model) is being silently discarded.

Recovery steps:

```powershell
# 1) Dump bytes around the reported offset to see what corrupted it.
$cfg = "$env:LOCALAPPDATA\hermes\config.yaml"
$position = 12201   # use the number from the error message
$bytes = [System.IO.File]::ReadAllBytes($cfg)
$start = [Math]::Max(0, $position - 30)
$end   = [Math]::Min($bytes.Length - 1, $position + 30)
for ($i = $start; $i -le $end; $i++) {
    $b = $bytes[$i]
    $c = if ($b -ge 0x20 -and $b -lt 0x7F) { [char]$b } else { '.' }
    "{0,6} : 0x{1:X2} '{2}'" -f $i, $b, $c
}

# 2) Restore the latest backup made by fix_hermes_64k.ps1.
$bak = Get-ChildItem "$cfg.bak.*" |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $bak) { throw "No .bak file -- cannot auto-restore." }
Copy-Item $bak.FullName $cfg -Force

# 3) Make the compression-model change via Hermes' own setter
#    (validates YAML on write):
hermes config set auxiliary.compression.model qwen2.5-32b-instruct-q4_K_M-64k

# 4) Also pick the main inference model interactively:
hermes model

# 5) Restart Hermes and verify the status bar is no longer "$ unknown":
hermes
```

If `hermes config set` rejects the nested key path, fall back to
`hermes config edit`, which opens `config.yaml` in `$env:EDITOR`
(set this first, e.g. `$env:EDITOR = "notepad"`) and validates on
save. Do **not** edit the file directly with `Set-Content` — the
PS5/PS7 encoding and line-ending defaults differ and have already
once produced an off-by-one corruption around offset 12201.

---

## 9. Common operator tasks

```powershell
# Show the last 50 Hermes log lines
hermes logs

# Follow Hermes logs in real time
hermes logs -f

# Show only error lines
hermes logs errors

# Inspect available tools / skills
hermes --help
```

For any change that would touch broker connectivity, the answer is
"not on this branch / not on this box".
