# Hermes Operating Runbook

> Operator-side procedures for running Hermes Agent (NousResearch/hermes-agent)
> against this repo. Mock-only by design. Companion to
> `docs/hermes_training_profile.md` (the project knowledge pack Hermes itself
> should ingest) and `docs/hermes_operator_runbook.md` (the broader Windows /
> Hermes startup runbook from Phase 4).

This runbook covers the day-to-day operating loop:

1. preflight smoke (no Hermes launch)
2. start Hermes
3. verify model + context
4. verify mock-only envelope
5. stop Hermes
6. troubleshooting (including Windows `hermes.exe` blocking)

---

## 1. Preflight smoke (every session start)

Run this first. It checks Ollama + the model + the project envelope + the
forbidden-credential blacklist WITHOUT launching Hermes.

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\hermes_smoke_test.ps1
```

Expected: ten or more `[PASS]` lines and `OVERALL : PASS  exit 0`.

Failing checks:

| Symptom | Fix |
|---|---|
| `[FAIL] Ollama not reachable on 127.0.0.1:11434` | Start Ollama (taskbar / `ollama serve`) |
| `[FAIL] qwen2.5-coder:14b-64k not in ollama list` | Build the 64K tag — see § 6 below |
| `[FAIL] LIVE_TRADING=true detected in session env` | Close shell, open a fresh PowerShell |
| `[FAIL] forbidden broker credential env 'X' is set` | Same: fresh shell; don't try to unset in place |
| `[FAIL] configs/hermes.env.example missing OLLAMA_MODEL=qwen2.5-coder:14b-64k` | Run `git diff configs/hermes.env.example` to see what drifted |

---

## 2. Start Hermes

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\start_hermes.ps1
```

What the launcher does (read `scripts/start_hermes.ps1` for the exact flow):

1. Pins mock envelope (`LIVE_TRADING=false / EXECUTION_MODE=mock /
   BROKER_MODE=mock / CUDA_VISIBLE_DEVICES=1 / PROJECT_ROOT / DATA_ROOT`).
2. Sets Ollama defaults: `OLLAMA_BASE_URL=http://127.0.0.1:11434`,
   `OLLAMA_MODEL=qwen2.5-coder:14b-64k`, `MODEL_CONTEXT_LENGTH=65536`,
   `OLLAMA_NUM_CTX=65536`.
3. Loads the allowlist (`C:\Trading\configs\hermes_allowlist.yaml`); refuses
   to launch if missing.
4. Optionally overlays `C:\Trading\configs\hermes.env`. **Ignores** any line
   that tries to flip the envelope vars.
5. Force-upgrades the model name: if any pre-existing `OLLAMA_MODEL` value
   is the stock `qwen2.5:14b` or `qwen2.5-coder:14b` (both 32K context),
   silently rewrites to `qwen2.5-coder:14b-64k` because Hermes Agent
   requires >= 64K context. Echoes a `[fix]` line so the operator sees it.
6. Re-checks the envelope vars and the broker-credential blacklist.
7. **Resolves the venv Python entrypoint** at
   `$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe`.
   * Aborts if not present (says "Hermes venv python not found").
   * **Ignores** `$env:HERMES_EXEC` even if it's set — see § 7 for why.
8. Verifies `from hermes_cli.main import main` imports cleanly via the venv
   python; aborts if it doesn't.
9. Echoes the full configuration table.
10. Launches Hermes via `& $env:HERMES_PY -c "from hermes_cli.main import
    main; raise SystemExit(main())" @args`.

Expected echo before launch:

```
Hermes launcher -- mock-only configuration
------------------------------------------
  LIVE_TRADING          = false
  EXECUTION_MODE        = mock
  BROKER_MODE           = mock
  CUDA_VISIBLE_DEVICES  = 1
  PROJECT_ROOT          = C:\Trading\ai_fibo_vision_trader
  DATA_ROOT             = D:\TradingData
  OLLAMA_BASE_URL       = http://127.0.0.1:11434
  OLLAMA_MODEL          = qwen2.5-coder:14b-64k
  MODEL_CONTEXT_LENGTH  = 65536
  OLLAMA_NUM_CTX        = 65536
  HERMES_ALLOWLIST      = C:\Trading\configs\hermes_allowlist.yaml
  HERMES_PY             = C:\Users\...\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
```

---

## 3. Verify model + context

Inside Hermes' TUI:

```
/model
```

Expected: the active model line shows `qwen2.5-coder:14b-64k` (or whatever
your local Hermes `config.yaml` `model.default` is set to).

Or from outside:

```powershell
# Model registered with the project's intended tag
ollama list | Select-String -Pattern 'qwen2\.5-coder:14b-64k'

# Verify the tag itself has 64K context
ollama show qwen2.5-coder:14b-64k | Select-String -Pattern 'context length'
# expect: context length   65536
```

Verify Hermes' own user-scope config:

```powershell
Get-Content "$env:LOCALAPPDATA\hermes\config.yaml" | Select-Object -First 22
# Expect:
#   model:
#     default: qwen2.5-coder:14b-64k
#     context_length: 65536
```

---

## 4. Verify mock-only envelope

Hermes inherits `start_hermes.ps1`'s pinned envelope. From inside the TUI
ask Hermes:

```
read configs/hermes_allowlist.yaml and the LIVE_TRADING env var; tell me
the current envelope state
```

Or just from another PowerShell window:

```powershell
# 1. Process env (what Hermes is running with)
Get-Process python | Where-Object { $_.MainModule.FileName -match 'hermes-agent\\venv' } |
    ForEach-Object {
        $envs = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)").CommandLine
        "PID=$($_.Id) CMD=$envs"
    }

# 2. Independent mock-only audit (does not need Hermes)
.\scripts\hermes_smoke_test.ps1   # re-runs all checks idempotently

# 3. The load-bearing trades.jsonl audit
Get-Content logs\trades.jsonl | Where-Object { ($_ | ConvertFrom-Json).mode -ne 'mock' }
# expect: NO output. Any output is a P0 incident.
```

---

## 5. Stop Hermes

Press `Ctrl+C` in the Hermes TUI window. Hermes installs its own SIGINT
handler and exits cleanly. The launcher exits with whatever Hermes returned.

If the TUI is unresponsive:

```powershell
Get-Process python | Where-Object {
    $_.MainModule.FileName -match 'hermes-agent\\venv'
} | Stop-Process -Force
```

There is no PID file for Hermes (unlike the Phase 5.E watch loop). The
process is identifiable by its Hermes-venv python path.

---

## 6. Troubleshooting: build the 64K Ollama tag

`qwen2.5-coder:14b` (no suffix) ships from Ollama with `num_ctx=32768`,
which is below Hermes Agent's >= 64K requirement. Build the 64K tag once:

```powershell
# 1. Dump the existing modelfile, patch num_ctx, create the new tag
ollama show qwen2.5-coder:14b --modelfile | Out-File -Encoding ascii .\qwen-coder-14b-64k.Modelfile
notepad .\qwen-coder-14b-64k.Modelfile
# In Notepad: find the line `PARAMETER num_ctx 32768` (or similar),
# change it to `PARAMETER num_ctx 65536`. Save.

# 2. Create the tag
ollama create qwen2.5-coder:14b-64k -f .\qwen-coder-14b-64k.Modelfile

# 3. Verify
ollama show qwen2.5-coder:14b-64k | Select-String -Pattern 'context length'
# expect: context length   65536

# 4. Optional: clean up
Remove-Item .\qwen-coder-14b-64k.Modelfile
```

`scripts/fix_hermes_64k.ps1` shows the same procedure for the 32B model
(`qwen2.5-32b-instruct-q4_K_M-64k`) — adapt that script if you want a
non-interactive version for the 14B.

---

## 7. Troubleshooting: Windows blocks `hermes.exe`

**Symptom**: launching Hermes via the legacy path (`& $env:HERMES_EXEC`)
silently does nothing, or `Get-Command hermes` returns nothing despite
Hermes being installed in `$env:LOCALAPPDATA\hermes\`.

**Cause**: Windows AppLocker / Smart App Control / WDAC (or corporate
policy) can block unsigned `.exe` files even from the user's own
`%LOCALAPPDATA%`.

**Fix**: the launcher already bypasses this by going through the venv
Python:

```
& $env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe `
    -c "from hermes_cli.main import main; raise SystemExit(main())"
```

`python.exe` (the project's venv python) is trusted by Windows, so the
launch goes through. Hermes ends up imported as a module and runs in
that python process. This is what the current `scripts/start_hermes.ps1`
does. `HERMES_EXEC` is **deliberately ignored** to prevent accidentally
falling back to the blocked binary.

To verify the venv python and entrypoint are both present:

```powershell
$py = "$env:LOCALAPPDATA\hermes\hermes-agent\venv\Scripts\python.exe"
Test-Path $py
& $py -c "from hermes_cli.main import main; print(main.__module__)"
# expect: hermes_cli.main
```

If `Test-Path` returns False: Hermes' venv was not created where expected.
Reinstall Hermes Agent, or set `$env:HERMES_PY` to the correct venv python
path before launching.

---

## 8. Common pitfalls

| Symptom | Likely cause | Fix |
|---|---|---|
| `Refusing to start: LIVE_TRADING=true` | Operator's shell has hot envelope | Close shell, open fresh PowerShell |
| `Refusing to start: forbidden broker credential env 'X'` | Cred env leaked from `.env` autoload | Same; never try to unset in place |
| Hermes loads but `/model` shows the wrong tag | Hermes' own `config.yaml` was edited out of sync | Reconcile: `Get-Content "$env:LOCALAPPDATA\hermes\config.yaml" \| Select-Object -First 22` |
| Hermes times out on first prompt | Ollama warming up the model (first load is 30-60 s for 14B) | Wait; subsequent prompts are fast |
| `context length` complaint from Hermes ("> 65536") | Custom prompt + history exceeds 64K | Use `/compress` inside Hermes, or start a new session |
| `hermes.exe` not found / blocked | Windows policy | § 7 — launcher already handles this; do NOT try `& hermes` directly |
| GPU 0 OOM | Other process holding VRAM on GPU 0 (display card) | `scripts/gpu_profile.ps1 -Strict` to identify; LLM should be on GPU 1 anyway (`CUDA_VISIBLE_DEVICES=1`) |

---

## 9. Operator Checklist

**Session start:**

- [ ] `cd C:\Trading\ai_fibo_vision_trader`
- [ ] `git status` — clean (only `?? logs/windows_diagnose.txt`)
- [ ] `$env:LIVE_TRADING, $env:EXECUTION_MODE, $env:BROKER_MODE` — `false / mock / mock` or empty
- [ ] `Get-ChildItem env: | Where-Object Name -match 'SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO'` — no output
- [ ] `.\scripts\hermes_smoke_test.ps1` — `OVERALL : PASS`
- [ ] `.\scripts\start_hermes.ps1` — echoes the mock-only config table, then enters TUI

**Session end:**

- [ ] Press `Ctrl+C` in Hermes TUI
- [ ] `Get-Process python | Where-Object { $_.MainModule.FileName -match 'hermes-agent' }` — no rows
- [ ] `Get-Content logs\trades.jsonl | Where-Object { ($_ | ConvertFrom-Json).mode -ne 'mock' }` — no output
- [ ] `git status` — same as session start (no unexpected modifications)

---

## 10. References

* `docs/hermes_training_profile.md` — what Hermes should KNOW (architecture, strategy, rules)
* `docs/hermes_operator_runbook.md` — broader Phase-4 Windows envelope (Hermes-side and Ollama-side recipes)
* `prompts/hermes_project_system_prompt.md` — copy-paste system prompt for the project
* `scripts/start_hermes.ps1` — the launcher (mock-only, Python entrypoint, model + context pinned)
* `scripts/hermes_smoke_test.ps1` — preflight without launching Hermes
* `configs/hermes_allowlist.yaml` — path + command allowlist (project documentation)
* `configs/hermes.env.example` — env overlay template (copy to `C:\Trading\configs\hermes.env`)
* `tests/test_hermes_safety.py` — invariants for the launcher + env + allowlist
