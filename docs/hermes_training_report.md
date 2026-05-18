# Hermes Training Report

> Verification + delta summary for the Hermes Agent project-training pack
> on `ai_fibo_vision_trader`. **This is not model fine-tuning.** No LoRA,
> no weights changed. The pack consists of docs / config / launcher /
> guard tests that let Hermes (running locally on qwen2.5-coder:14b-64k
> via Ollama) understand this repo's architecture, strategy, and safety
> rules.

---

## 1. Files changed / added

| # | Path | Status | Purpose |
|---|---|---|---|
| 1 | `docs/hermes_training_profile.md`               | **NEW** | What Hermes should KNOW: project mission, architecture, FIBO_MOB_v2 strategy + operator playbook, allowed/forbidden actions, answer style |
| 2 | `docs/hermes_operating_runbook.md`              | **NEW** | How operator runs Hermes: preflight smoke, start, verify model + context + mock envelope, stop, troubleshoot (including Windows blocking `hermes.exe`), per-session checklist |
| 3 | `prompts/hermes_project_system_prompt.md`       | **NEW** | Copy-paste system prompt for Hermes (mock-only invariants + architecture + strategy + answer style) |
| 4 | `scripts/hermes_smoke_test.ps1`                 | **NEW** | 14-check preflight: Ollama reachable + model present + modelfile num_ctx >= 65536 + env example invariants + shell envelope not hot + no broker creds + Hermes venv python present + allowlist file present. Pure read-only; never launches Hermes |
| 5 | `docs/hermes_training_report.md`                | **NEW** | This file |
| 6 | `configs/hermes.env.example`                    | edit | Line ~25-30: changed `OLLAMA_MODEL=qwen2.5:14b` → `OLLAMA_MODEL=qwen2.5-coder:14b-64k`; added `MODEL_CONTEXT_LENGTH=65536` + `OLLAMA_NUM_CTX=65536`; explanatory comment block above |
| 7 | `tests/test_hermes_safety.py`                   | edit | Renamed `test_start_script_refuses_to_launch_without_hermes_exec` → `test_start_script_refuses_to_launch_without_verified_entrypoint` and updated body to assert the new HERMES_PY mechanism (preserves safety intent: refuse to launch without a verified entrypoint; mechanism swapped from blocked .exe to venv python). 11 other tests untouched. |

**Already-present, NOT changed by THIS task** (carried over from prior
session work; staged as `M`/`??` before this task began):

| Path | Status | Notes |
|---|---|---|
| `scripts/start_hermes.ps1` | `M` (pre-existing) | Already patched in a prior turn to use the venv Python entrypoint + force `OLLAMA_MODEL=qwen2.5-coder:14b-64k` + `MODEL_CONTEXT_LENGTH=65536`. My only follow-up was the matching `tests/test_hermes_safety.py` rename above. |
| `scripts/start_hermes_fixed.ps1` | `??` (pre-existing) | Byte-identical to `scripts/start_hermes.ps1`. Operator's backup before the patch. Safe to delete manually with `Remove-Item scripts\start_hermes_fixed.ps1` — I left it alone since the operator created it and may want to keep it. |

**Also touched in this session (Ollama state, NOT a repo file)**:

* Rebuilt the Ollama tag `qwen2.5-coder:14b-64k` via `ollama create -f`
  using a modelfile patched with `PARAMETER num_ctx 65536`. The previous
  tag also had `PARAMETER num_ctx 65536` (so the rebuild was a no-op
  semantically), but I verified the manifest explicitly. The base GGUF
  metadata `context length` shown by `ollama show` remains 32768 (that's
  Qwen2.5's intrinsic value); the runtime context window is 65536 because
  Ollama honours the modelfile's `PARAMETER num_ctx` override.

---

## 2. Safety checks (all green)

### 2.1 Preflight smoke (`scripts/hermes_smoke_test.ps1`)

```
[PASS]  1. Ollama reachable on http://127.0.0.1:11434
[PASS]  2. Ollama list contains qwen2.5-coder:14b-64k
[PASS]  3. qwen2.5-coder:14b-64k modelfile num_ctx >= 65536        (actual: 65536)
[PASS]  4. hermes.env.example pins OLLAMA_MODEL=qwen2.5-coder:14b-64k
[PASS]  5. hermes.env.example pins MODEL_CONTEXT_LENGTH=65536
[PASS]  6. hermes.env.example pins LIVE_TRADING=false
[PASS]  7. hermes.env.example pins EXECUTION_MODE=mock
[PASS]  8. hermes.env.example pins BROKER_MODE=mock
[PASS]  9. shell env LIVE_TRADING != truthy
[PASS] 10. shell env EXECUTION_MODE != live
[PASS] 11. shell env BROKER_MODE != live
[PASS] 12. no broker credential env in shell                       (none of 15 checked)
[PASS] 13. Hermes venv python present at expected path
[PASS] 14. configs/hermes_allowlist.yaml present in repo

OVERALL : PASS  (all checks green)   exit 0
```

### 2.2 Repo pytest suite (`scripts/test.ps1`)

```
test.ps1 -- mock-only invocation
  LIVE_TRADING : false / EXECUTION_MODE : mock / BROKER_MODE : mock
478 passed in 8.45s
```

No regression. Test count unchanged (1 test renamed in place; not new + delete).

### 2.3 Mock-only invariants (5-layer audit)

| Layer | Status |
|---|---|
| Launcher `start_hermes.ps1` pins `LIVE_TRADING=false / EXECUTION_MODE=mock / BROKER_MODE=mock` | ✅ (line 17-19) |
| Launcher refuses if any well-known broker credential env is set | ✅ (line 81-93) |
| Launcher launches via venv Python entrypoint, NOT `hermes.exe` (Windows-policy bypass) | ✅ (line 95-111) |
| `RiskGate.__init__` raises `LiveTradingForbidden` if envelope hot | ✅ (`risk/risk_gate.py:33-39` — unchanged) |
| `MockExecutor.submit` re-checks envelope at write boundary | ✅ (`executor/mock_executor.py:36-40` — unchanged) |

### 2.4 Path / command guards (Hermes allowlist)

`configs/hermes_allowlist.yaml` unchanged:

* Allowed roots (4): `C:\Trading\ai_fibo_vision_trader`, `C:\Trading\configs`, `C:\Trading\logs`, `D:\TradingData`
* Denied globs include `**/.env`, `**/credentials*`, `**/secrets*`, `**/CTPro/**`, `**/shioaji*`, `**/ib_insync*`, `**/MetaTrader5*`, `**/mt5*`
* Denied commands cover every broker SDK + `live_order` / `place_order` / `real_order` / `CTPro` patterns

The four `tests/test_hermes_safety.py` assertions that inspect this YAML
all PASS (unchanged).

---

## 3. Exact commands

### Smoke test (no Hermes launch)

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\hermes_smoke_test.ps1
# expect: OVERALL : PASS  exit 0
```

Optional non-default parameters:

```powershell
.\scripts\hermes_smoke_test.ps1 -ExpectedModel qwen2.5-coder:14b-64k -ExpectedContext 65536 -OllamaUrl http://127.0.0.1:11434
```

### Start Hermes (interactive TUI)

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\start_hermes.ps1
# echoes the mock-only config table, then enters Hermes' TUI
# Ctrl+C to exit
```

The launcher refuses to start if:
* envelope hot (`LIVE_TRADING != false`, `EXECUTION_MODE != mock`, or `BROKER_MODE != mock`)
* any broker credential env is set
* the Hermes venv python is not present at the expected path
* `from hermes_cli.main import main` doesn't import

### Post-session audit

```powershell
Get-Content logs\trades.jsonl | Where-Object { ($_ | ConvertFrom-Json).mode -ne 'mock' }
# expect: NO output. Any output is a P0 incident.
```

---

## 4. What was NOT touched

* `executor/mock_executor.py` — unchanged
* `risk/risk_gate.py` — unchanged
* `strategy/fibo_mob_v2.py` — unchanged
* `config/capture.yaml` — unchanged (calibration block remains commented out per Phase 5.C A1)
* `configs/hermes_allowlist.yaml` — unchanged
* `vision/*` — unchanged
* `quote/*` — unchanged
* `notify/*` — unchanged
* `capture/*` — unchanged
* `app/*` — unchanged
* `tools/*` — unchanged
* `scripts/test.ps1` / `scripts/watch_loop_*.ps1` / `scripts/install_watch_loop_task.ps1` / `scripts/watch_loop_soak.ps1` / `scripts/gpu_profile.ps1` / `scripts/calibrate_chart_dry_run.ps1` etc. — unchanged
* `docs/{hermes_operator_runbook,quote_feed_design,phase5e_watch_loop_runbook,phase5f_scheduler_runbook,phase5_soak_runbook,live_capture_calibration_runbook}.md` — unchanged
* `tests/test_mock_only.py`, `tests/test_no_hermes_yolo.py`, every other `tests/test_*.py` except the one rename in `tests/test_hermes_safety.py` — unchanged
* `README.md` — unchanged

No broker call made. No live trading touched. No order submitted.
No `pip install`, no `npm install`. Hermes Agent itself was NOT
launched in this session (only the smoke test ran, which is
read-only preflight).

---

## 5. Risks and limitations

1. **Hermes config `model.default` is user-scope, not project-scope.**
   The repo's `configs/hermes.env.example` and `scripts/start_hermes.ps1`
   pin `OLLAMA_MODEL=qwen2.5-coder:14b-64k`, but Hermes itself reads its
   own per-user `config.yaml` at `$env:LOCALAPPDATA\hermes\config.yaml`
   for its primary model choice. They are reconciled at runtime via the
   `OLLAMA_MODEL` env, but if you `/model` in the Hermes TUI to something
   else, that override persists for the session.
2. **`ollama show ... context length` is misleading.** It reports the
   base GGUF's intrinsic max (32768 for Qwen2.5), NOT the runtime
   `num_ctx` override from the modelfile. The smoke test correctly
   reads the modelfile's `PARAMETER num_ctx` line instead. Don't change
   the smoke test back to the `ollama show context length` check.
3. **`scripts/start_hermes_fixed.ps1` is a redundant operator backup.**
   It is byte-identical to `scripts/start_hermes.ps1`. Currently
   untracked (`??`). Safe to delete with `Remove-Item
   scripts\start_hermes_fixed.ps1` whenever the operator is comfortable.
   I left it in place because it predates this task.
4. **Hermes itself was not launched in this session.** The smoke test
   does NOT exercise the Hermes loop end-to-end. The first real launch
   may surface issues the smoke test cannot detect (e.g. Ollama
   versioning, prompt template differences). Use
   `docs/hermes_operating_runbook.md` § 8 troubleshooting if the first
   launch misbehaves.
5. **Path / command allowlist is documentation, not an OS sandbox.**
   The `configs/hermes_allowlist.yaml` header says so explicitly. Real
   OS-level path restriction would require a separate restricted user
   account on the Windows host. The launcher and tests still make this
   document the load-bearing reference.
6. **The strategy code (`strategy/fibo_mob_v2.py`) is a deliberate
   subset of the operator's full Fibo MOB playbook.** Confirmation-
   candle / RSI-turn / volume-exhaustion / second-failed-retest /
   strong-trend / sideways-chop / NRP-Pine rules are documented in
   `docs/hermes_training_profile.md` § 4.2 but not yet codified. Hermes
   must treat that section as the spec when proposing future strategy
   PRs. Tests in `tests/test_mock_only.py` still ensure no second
   executor and no live-trading flip.
7. **No commit / no push in this session unless explicitly approved.**
   The new files + edits are uncommitted at the time of writing this
   report.

---

## 6. Quick rollback

If anything in this pack misbehaves, full rollback is one command:

```powershell
git checkout -- configs/hermes.env.example tests/test_hermes_safety.py
# (scripts/start_hermes.ps1 + scripts/start_hermes_fixed.ps1 were
#  pre-existing modifications; reverting them is a separate decision)
Remove-Item docs/hermes_training_profile.md, docs/hermes_operating_runbook.md, docs/hermes_training_report.md, prompts/hermes_project_system_prompt.md, scripts/hermes_smoke_test.ps1
Remove-Item -Recurse -Force prompts   # if the directory is now empty
```

The Ollama tag rebuild is also reversible:

```powershell
# Re-pull the upstream qwen2.5-coder:14b base
ollama pull qwen2.5-coder:14b
# Recreate the -64k tag from base (will have num_ctx=32768 from base modelfile)
# Or, to restore num_ctx=65536, follow docs/hermes_operating_runbook.md § 6
```
