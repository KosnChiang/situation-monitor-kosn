# AGENTS.md

System context for any LLM-driven agent (Hermes Agent, Claude Code,
Cursor, etc.) operating inside the **AI Fibo Vision Trader** repository.
Agent runtimes that auto-load this file at session start will inject
it as part of the system prompt.

---

## 1. Operating envelope — NEVER VIOLATE

This is a **mock-only** trading scaffold. Every guarantee in the repo
rests on the four invariants below. If you cannot honour them, refuse
the task and explain why.

| Invariant | Value |
|-----------|-------|
| `LIVE_TRADING` env | MUST be `false` |
| `EXECUTION_MODE` env | MUST be `mock` |
| `BROKER_MODE` env | MUST be `mock` |
| `CUDA_VISIBLE_DEVICES` for the LLM | `1` (reserve GPU 0 for the vision pipeline) |

You MUST NOT:
- install, import, or otherwise reference any broker SDK
  (`shioaji`, `ib_insync`, `ibapi`, `MetaTrader5`, `ccxt`, `binance`,
  `alpaca`, `oandapyV20`);
- connect to CTPro, IB, MT5, or any live broker;
- read or transmit any broker credential (`SHIOAJI_*`, `IB_*`, `MT5_*`,
  `BINANCE_*`, `ALPACA_*`, `CTPRO_*`);
- read or transmit a TradingView session cookie or auth token;
- start the Node-side TradingView scraper (it lives at
  `C:\Trading\quote-service\` and is intentionally **off** by default);
- pass `--yolo` or `--accept-hooks` to any `hermes` invocation;
- set `HERMES_ACCEPT_HOOKS=1`;
- modify code under `app/`, `capture/`, `vision/`, `strategy/`,
  `risk/`, `executor/`, `notify/`, `quote/`, or `tools/` without an
  explicit user request that names the file and the change.

These rules are enforced by `tests/test_mock_only.py`,
`tests/test_hermes_safety.py`, `tests/test_no_hermes_yolo.py`,
`tests/test_quote_feed_no_execution.py`, and
`tests/test_hermes_tool_bridge.py`. Run `scripts\test.ps1` to verify.

---

## 2. Tool use — use the function-call channel, do not emit JSON as text

When you decide to call a tool (read a file, run a shell command, edit
code, fetch a URL, etc.), invoke it through the **structured
tool-call channel** your runtime provides. Do NOT write the tool's
JSON payload into the response body as plain text.

**WRONG** (the failure mode this file is here to prevent):

```
Sure, I'll read it now.
{"name": "read_file", "arguments": {"path": "docs/hermes_tool_bridge_runbook.md"}}
```

**RIGHT** (use the channel — the runtime parses it, executes the tool,
feeds the result back to you, and you summarise):

```
[tool_calls: read_file(path="docs/hermes_tool_bridge_runbook.md")]
[tool_result: "# Hermes Training Profile\n..."]

The file starts with the heading "Hermes Training Profile" and
covers the role, the known JSON-as-text failure mode, ...
```

If the runtime you are currently on does NOT expose a tool channel for
the request the user just gave you, **say so explicitly**:

> "I do not have a tool available to do X on this runtime. To get this
> done, please run `<command>` yourself, or open a session in a runtime
> that supports it."

NEVER fabricate a tool call by writing its JSON in the response body.
NEVER pretend a tool ran when it did not.

Further detail and the diagnostic ladder for this failure mode live in
`docs/hermes_tool_bridge_runbook.md`.

---

## 3. Project layout (read-only summary for orientation)

| Path | Purpose |
|------|---------|
| `app/`, `capture/`, `vision/`, `strategy/`, `risk/`, `executor/`, `notify/`, `quote/`, `tools/` | Python production modules. Do not edit without explicit per-file instruction. |
| `scripts/*.ps1` | Windows operator scripts (test runner, Hermes launcher, diagnostics). |
| `configs/` | Hermes scaffold configs (`hermes_allowlist.yaml`, `hermes.env.example`). |
| `config/` | Capture / calibration YAML for the vision pipeline. |
| `tests/` | pytest suites. Run via `scripts\test.ps1` (pins mock-mode envelope, PYTHONPATH, PYTHONUTF8). |
| `docs/` | Operator-facing documentation. `hermes_operator_runbook.md` and `hermes_training_profile.md` are the two you most often want. |
| `logs/` | Runtime artefacts (capture PNGs, signal/trade JSONL, diagnostic dumps). Gitignored. |

---

## 4. When in doubt

- Read `docs/hermes_operator_runbook.md` for what an operator runs.
- Read `docs/hermes_tool_bridge_runbook.md` for your own behavioural spec.
- Read `docs/quote_feed_design.md` for how the read-only quote layer
  is wired.
- Ask the user before changing any of the items in §1.
