# Hermes Tool-Bridge Runbook

Failure-mode runbook for the **Hermes Agent ↔ Ollama tool channel**
on this project. Covers the symptom where Hermes emits a tool-call
JSON in its response body instead of actually invoking the tool.

Read together with:
* `AGENTS.md` — the condensed system-prompt every agent runtime
  injects at session start;
* `docs/hermes_training_profile.md` — the broader project training
  pack (architecture, safety rules, FIBO_MOB_v2 strategy);
* `docs/hermes_operator_runbook.md` — operator-side procedures.

This runbook also ships because asking Hermes to read a file that
does not exist (the original `docs/hermes_training_profile.md`
reference, before that file was actually written) was one observed
contributor to the JSON-as-text failure mode documented in §2.

---

## 1. Role

Hermes is the **dialogue + planning + tool-orchestration** layer for
the AI Fibo Vision Trader. Concretely, on this host Hermes:

* talks to a local Ollama instance at `http://127.0.0.1:11434/v1`.
  The **inference (chat) model** is `qwen2.5-coder:14b-64k` (set in
  `$env:LOCALAPPDATA\hermes\config.yaml` `model.default`, pinned by
  `configs/hermes.env.example`). The **compression model** is
  `qwen2.5-32b-instruct-q4_K_M-64k` (built by
  `scripts/fix_hermes_64k.ps1`), retained as a fallback in
  `auxiliary.compression.model` and `custom_providers[Local]`;
* reads project files via its file tool;
* runs shell commands via its terminal tool (subject to
  `configs/hermes_allowlist.yaml`);
* is started **only** through `scripts/start_hermes.ps1`, which pins
  the mock-only envelope and refuses to launch if any broker
  credential env name is set.

Hermes does **not** place orders, does **not** import any broker SDK,
does **not** read TradingView session tokens, does **not** invoke the
Node-side quote scraper at `C:\Trading\quote-service\`. All of those
are blocked at the project / launcher / test layers.

---

## 2. Known failure mode: tool-call JSON emitted as plain text

### 2.1 Symptom

Operator asks Hermes a tool-using question, e.g.

> "Read `docs/hermes_training_profile.md` and tell me what §3 says."

Hermes' reply contains literal tool-call JSON in the response body
instead of actually invoking the tool. Examples of what the reply
looks like in the broken state:

```
I will read the file now.
{"name": "read_file", "arguments": {"path": "docs/hermes_training_profile.md"}}
```

```
<tool_call>
{"name":"read_file","arguments":{"path":"docs/hermes_training_profile.md"}}
</tool_call>
```

```
I'll use the read_file tool: read_file({"path": "docs/hermes_training_profile.md"})
```

In all three the tool **was not actually executed**. The conversation
proceeds as if the agent had read the file, but no read happened, so
any follow-up referring to "what the file says" is a hallucination.

### 2.2 Why it happens with the Qwen2.5 family on Ollama

Three factors usually combine. The symptom has been observed on
`qwen2.5-coder:7b-64k` (most aggressive), `qwen2.5-coder:14b-64k`
(intermittent), and `qwen2.5-32b-instruct-q4_K_M-64k` (intermittent).
The current project default is `qwen2.5-coder:14b-64k`; smaller tags
make the failure mode more frequent, not different in kind.

1. **Qwen2.5 chat / coder fine-tunes** target a Qwen-specific function
   calling format (`<|im_start|>...<|im_end|>` blocks with a tool
   listing in the system prompt), not the OpenAI-style `tool_calls`
   that Hermes sends through Ollama's OpenAI bridge.
2. **Ollama's OpenAI-compatibility layer** translates OpenAI
   `tools=[...]` into a prompt-template instruction. With models that
   were not heavily trained on that exact template, the model emits
   the tool-call payload as content text rather than into the
   structured `tool_calls` channel. The smaller the model, the more
   often this falls through.
3. The **target file did not exist**. When asked to read a missing
   file, an under-trained-for-tools model often falls back to
   "describe what I would have done" instead of actually attempting
   the call. Creating this file removes that excuse.

### 2.3 Why we documented it instead of silently swapping models

The inference and compression model decisions live in
`$env:LOCALAPPDATA\hermes\config.yaml` (operator-owned) and
`scripts/fix_hermes_64k.ps1` (compression-tag build recipe).
Quietly swapping models to fix tool-calling would also affect
compression quality, GPU residency, and VRAM budgeting — those are
deliberate operator decisions, not an agent fix. The 2026-05-19
revert from `qwen2.5-coder:7b-64k` back to `qwen2.5-coder:14b-64k`
was done by the operator after `scripts/hermes_tool_call_smoke.ps1`
flagged the 7B regression; see `docs/hermes_operating_runbook.md`
section 7.5.

---

## 3. Three workarounds, in order of disruption

### A. Improve the system prompt — least disruption

Add `AGENTS.md` to the repo root (this commit). Hermes auto-injects
it at session start; it includes an explicit "use the tool-call
channel, do not emit JSON as text" instruction, plus a worked
WRONG/RIGHT example. Several model families respond to this directly.

Verify with `scripts\hermes_tool_bridge_smoke.ps1` (see §5 below).

### B. ReAct-style fallback prompt — moderate

If A is insufficient, instruct the operator to launch Hermes with a
session-level prompt that demands a Thought / Action / Observation
loop in markdown, and have Hermes' file tool intercept lines that
match that structure. This requires no model swap, but the operator
must remember to use the ReAct preamble. Worked example:

> System: For every tool you decide to use, format your reply as:
>   `Thought: <reasoning>`
>   `Action: <tool_name>(arg=...)`
> Wait for the runtime to return `Observation: <result>` and continue
> from there. Do NOT write the call payload as a JSON object in your
> reply body.

### C. Swap the inference model — most disruption

If A and B both fail, swap to a model fine-tuned for OpenAI-style
function calling. Candidates known to work well on Ollama:

* `nousresearch/hermes-2-pro-llama-3-8b` (8B, fast, native tool-call
  format that Ollama maps cleanly);
* `firefunction-v2` (function-calling specialist; 70B-class so
  needs more VRAM);
* `xlam-7b-r` (Salesforce, well-trained for tool use).

Procedure: pull the new model with `ollama pull`, then update
`auxiliary.compression.model` AND the main inference model in
`~/AppData\Local\hermes\config.yaml` using
`hermes config set` (do NOT raw-edit the YAML; see
`scripts/fix_hermes_64k.ps1` history for why).

This is a real operator decision (affects compression, VRAM, latency)
and is out of scope for an agent to do unilaterally.

---

## 4. What an agent observing this failure should do

If you (Hermes / Claude Code / any agent reading this) realise you
are about to emit a tool-call JSON in your response body, STOP and:

1. Check whether your runtime exposes a tool channel for this request.
2. If yes, use it. The response body should contain only natural
   language; the tool invocation lives in the structured channel.
3. If no (the runtime does not expose a tool you need), say so
   explicitly and stop. Do not fake it.
4. If you do not know whether your runtime exposes the channel, ask
   the user once: *"I am about to call X. Confirm your runtime
   surfaces tool calls for me?"*

Never fabricate a `tool_result`. Never claim to have read a file you
did not read. Never claim to have run a command you did not run.

---

## 5. Verification — `scripts\hermes_tool_bridge_smoke.ps1`

Run from the project root:

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\hermes_tool_bridge_smoke.ps1
```

What it does:

1. Pre-flight: refuses to run unless
   `LIVE_TRADING=false`, `EXECUTION_MODE=mock`, `BROKER_MODE=mock`.
2. Sends one prompt to Hermes via `hermes -z` (one-shot mode):
   *"Read `AGENTS.md` from the current directory and reply with ONLY
   the first markdown heading line (a line beginning with `#`)."*
3. Heuristics on the reply:
   * `PASS` if reply contains a `# `-prefixed line that matches the
     first heading of `AGENTS.md`;
   * `FAIL (json-as-text)` if reply contains literal tool-call JSON
     markers (`"tool_calls"`, `"function":`, `<tool_call>`, or a bare
     `read_file({` pattern);
   * `FAIL (no tool)` if reply states the agent could not call a
     tool;
   * `FAIL (other)` otherwise.

Pass means the tool channel works; you can proceed with normal
operator tasks. Fail means apply workarounds A → B → C in §3 in
order.

The smoke script intentionally avoids `--yolo` and `--accept-hooks`.
It uses `-z`, which auto-bypasses interactive approvals (necessary for
pipe mode), but the prompt itself is scoped to a single read of a
file that is in the project root, so the blast radius of `-z` here is
constrained to that one read.

---

## 6. If the smoke test fails — diagnostic ladder

| Step | Command | What to confirm |
|------|---------|-----------------|
| 1 | `hermes --version` | v0.14.0 or newer |
| 2 | `hermes config \| Select-String -Pattern 'provider\|model\|base_url'` | provider=`custom`, base_url=`http://127.0.0.1:11434/v1` |
| 3 | `ollama ps` | qwen2.5-32b tag is loaded (CPU or GPU); CONTEXT field shows the runtime context |
| 4 | `Invoke-WebRequest http://127.0.0.1:11434/v1/models` | status 200, lists the 64k tag |
| 5 | re-run smoke after rebooting Ollama daemon | sometimes a stale model handle eats tool_calls |
| 6 | apply workaround A (this commit's `AGENTS.md`) and re-smoke | if still failing, go to B |
| 7 | apply workaround B (ReAct preamble in your session) and re-smoke | if still failing, go to C |
| 8 | apply workaround C (model swap) — operator-led, NOT agent-led | requires explicit operator approval |

---

## 7. What this file is NOT

This is a behavioural spec for the agent, NOT a place to store
broker credentials, broker SDK install commands, live-trading
instructions, or any account / API key. None of those belong
anywhere in this repo. They are explicitly forbidden by
`AGENTS.md` §1 and by the test suite in `tests/`.
