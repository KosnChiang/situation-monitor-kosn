# Read-only quote feed — design

Phase-5.5 scaffold. Decided after the user vetoed installing
`Mathieu2301/TradingView-API` (a Node.js WebSocket scraper) directly
into the trading project and chose Path A: build a **read-only quote
feed module behind an abstract interface**, keep mock-only on the
execution side, decide on the concrete provider later.

## Why this exists at all

Through Phase 5 the rule was simply "no real market data". That made
unit-level reasoning trivial — the signal generator ate a CLI
`--price-y` and the executor was untouched. Once we accept *any*
real-time feed, the rule has to refactor: "mock-only" now applies to
**execution and credentials**, not to "no external data at all".

This document is the contract that keeps the new degrees of freedom
from leaking into execution.

## Ten hard constraints (user spec, verbatim)

1. May fetch real market data, but **read-only**.
2. No order placement.
3. No CTPro.
4. No broker / brokerage credential reads.
5. No broker SDK installs.
6. No `LIVE_TRADING=true`.
7. Quote service may **not** call `MockExecutor` or any executor.
8. No reading of TradingView cookie / session token.
9. No use of authenticated TradingView session.
10. Quote feed and order execution **may not share a process**.

## Architecture

```
+----------------------------+        file IPC        +-------------------------------+
| tools.quote_feed           |  ---- writes JSONL --->| logs/quotes.jsonl (append)    |
|   provider: MockQuote...   |                         +-------------------------------+
|   no executor, no broker   |                                       |
|   no credentials           |                          tails latest |
+----------------------------+                                       v
                                          +----------------------------------------+
                                          | tools.mock_fibo_signal                 |
                                          |   --price-source latest_quote          |
                                          |   reads logs/quotes.jsonl by path      |
                                          |   never imports quote.* in-process     |
                                          +----------------------------------------+
                                                              |
                                       (optional --submit)    v
                                          +----------------------------------------+
                                          | risk.RiskGate -> executor.MockExecutor |
                                          |   refuses LIVE_TRADING=true            |
                                          |   writes logs/trades.jsonl (mode=mock) |
                                          +----------------------------------------+
```

Two processes, one file. The file is the contract.

## Module layout

| File                                | Layer       | May import                              | Forbidden imports                   |
| ----------------------------------- | ----------- | --------------------------------------- | ----------------------------------- |
| `quote/quote_provider.py`           | quote (read) | stdlib                                  | executor, risk, strategy, broker SDK |
| `tools/quote_feed.py`               | quote (read) | stdlib + `quote.*`                      | executor, risk, strategy, broker SDK |
| `tools/mock_fibo_signal.py`         | exec        | stdlib + `risk.*` + `executor.*` + `strategy.*` | `quote.*`, broker SDK, network libs  |

The "may not import quote.*" rule on `mock_fibo_signal.py` is the
enforcement of constraint #10: the execution-side script cannot
in-process spawn a quote provider, so quote-feed crashes or
provider misbehavior cannot take the executor down with them.

## Quote schema (the JSONL contract)

Each line of `logs/quotes.jsonl` is a JSON object with **exactly**
these fields:

```json
{
  "symbol": "XAUUSD",
  "bid":    2399.5,
  "ask":    2400.5,
  "last":   2400.0,
  "mid":    2400.0,
  "ts":     1779043991.64,
  "timestamp": "2026-05-17T18:53:11.641746+00:00",
  "source": "mock"
}
```

`source` is the provider's `name` class attribute. Today only
`"mock"` exists; future providers must declare their own short name
(e.g. `"tradingview-ws"`, `"yfinance"`, …).

## Provider abstraction

`QuoteProvider` is an `abc.ABC` with one abstract method, `fetch`.
Construction enforces `LIVE_TRADING == "false"` as defense in depth;
the read layer cannot execute orders, but a hard refusal at
construction makes mock-mode envelope drift loud instead of silent.

`MockQuoteProvider` is the only concrete provider as of Phase 5.5.
It does a seeded Gaussian random walk around a configurable base
price. It never touches the network, never reads a credential.

Adding a new provider (future work):

1. Subclass `QuoteProvider` in `quote/<name>.py`.
2. Set `name = "<short slug>"`.
3. Implement `fetch(symbol) -> Quote`.
4. Register in `PROVIDERS = {...}` at the bottom of `quote/quote_provider.py`.
5. The static guards in `tests/test_quote_feed_no_execution.py` apply
   automatically (they scan everything under `quote/`).

A real provider that needs an *unauthenticated* upstream
(public TradingView WebSocket, public REST quote endpoints) is OK.
A provider that needs an authenticated session, a TradingView
cookie, or any broker login is **forbidden** by tests 7-9 and by
constraints 8-9.

## Process separation rationale

Three independent reasons:

1. **Failure isolation.** A misbehaving provider (network stall,
   parser bug, rate-limit ban) cannot take the executor offline. The
   executor side simply gets stale or empty `quotes.jsonl` and emits
   FLAT.
2. **Trust isolation.** The quote-feed process can be run under a
   restricted user / firewall rule that allows outbound to the
   upstream data source only. The executor process can be air-gapped
   from outbound network entirely.
3. **Auditability.** Every input to the executor is a JSONL line on
   disk with a timestamp. Replay of "what did the executor see and
   when" is a `tail logs/quotes.jsonl` and a `grep logs/signals.jsonl`,
   not a re-run of network calls.

## Operating procedures

### Run the quote feed (one process)

```powershell
cd C:\Trading\ai_fibo_vision_trader
$env:LIVE_TRADING   = "false"   # quote provider refuses to start otherwise
$env:EXECUTION_MODE = "mock"
.\.venv\Scripts\python.exe -m tools.quote_feed `
    --symbol XAUUSD --provider mock --interval 1 --max-iterations 0
```

`--max-iterations 0` loops forever; Ctrl+C exits cleanly.

### Run the signal generator (a second, independent process)

```powershell
cd C:\Trading\ai_fibo_vision_trader
$env:LIVE_TRADING   = "false"
$env:EXECUTION_MODE = "mock"
$env:BROKER_MODE    = "mock"
.\.venv\Scripts\python.exe -m tools.mock_fibo_signal `
    --price-source latest_quote --tolerance-px 8 --symbol XAUUSD --submit
```

The signal generator reads the most recent line of
`logs/quotes.jsonl`. If the file is missing or empty, it emits FLAT
and exits 0 — never crashes.

## What this scaffold deliberately does NOT do

* No installation of `Mathieu2301/TradingView-API` or any Node
  package (tests 8-9 enforce this).
* No `package.json`, no `node_modules/`, no `yarn.lock`.
* No TradingView authentication code.
* No `Cookie:` header anywhere.
* No real-network quote provider yet — adding one is a follow-on
  phase that picks a concrete upstream and writes the subclass.
* No backpressure / file-rotation strategy for `logs/quotes.jsonl`.
  At 1 quote/sec it grows ~30 MB/day; operators rotate it manually
  or accept that growth until a follow-on phase wires logrotate.

## Failure modes and signal generator behaviour

| quote feed state                                    | signal generator behaviour          |
| --------------------------------------------------- | ----------------------------------- |
| not started, `logs/quotes.jsonl` missing            | FLAT, reason "file does not exist"  |
| started but written 0 rows yet                      | FLAT, reason "file is empty"        |
| last row has corrupt JSON                           | FLAT, reason "last line not JSON"   |
| last row missing `last` field                       | FLAT, reason "missing 'last' field" |
| last row OK, but no Fibo line within tolerance      | FLAT (Phase-5 logic)                |
| last row OK, line in tolerance but conf < threshold | FLAT (Phase-5 logic)                |
| last row OK, in tolerance, conf >= threshold        | LONG (Phase-5 logic, MVP convention) |

Every FLAT path is logged to `logs/signals.jsonl`; the executor is
never invoked for FLAT, so `logs/trades.jsonl` stays at the
"mock-fill-only" length it had before the run.
