# Micro-Live Readiness Checklist

This is the gated checklist that MUST be completed and operator-signed
before any real-money order is placed via this codebase.

Each line is hard-pass / hard-fail. Partial passes do not count.

Print this page. Walk through it physically. Tick each box only after
the check is actually done -- not after you "intend to do it".

---

## Section A: Code readiness (the repo)

- [ ] `scripts\test.ps1` reports 0 failed (record total here: ______ passed).
- [ ] Latest commit on the branch is push-clean (`git status -sb` shows
      no ahead/behind, working tree clean except `.claude/`).
- [ ] All phase guards green:
      `test_no_live_trading_phase5*.py`, `test_no_live_trading_phase6*.py`,
      `test_no_hermes_yolo.py`, `test_quote_feed_no_execution.py`,
      `test_hermes_safety.py`, `test_mock_only.py`.
- [ ] `dry_run_live_order` with `--adapter-source=fake` runs end-to-end
      successfully today (record decision_id and exit code: ______).
- [ ] `dry_run_live_order` with `--adapter-source=external` and
      `FAKE_LIVE_ADAPTER=true` runs end-to-end (this proves the loader
      branch works without touching the real broker).

## Section B: Adapter readiness (the operator's plugin)

- [ ] An out-of-tree adapter exists at
      `C:\Trading\live-adapters\<broker>\live_adapter.py`.
- [ ] The adapter has been smoke-tested against the broker's paper /
      simulator account for at least 50 orders (record adapter version
      and last test date: ______).
- [ ] The adapter's `submit_order` returns within 1 second on the
      happy path.
- [ ] The adapter's `submit_order` raises (does NOT hang) on broker
      disconnect.
- [ ] The adapter writes to `logs/live_orders.jsonl` and
      `logs/live_fills.jsonl` via the main repo's `live.audit_log`
      helpers.
- [ ] The adapter does NOT write to `logs/trades.jsonl` or
      `logs/paper_trades.jsonl`.
- [ ] The adapter imports `keyring` (in its own venv) and reads
      credentials from Windows Credential Manager.
- [ ] The adapter does NOT read credentials from `.env`, CLI args,
      env vars, or hard-coded strings.

## Section C: Credentials

- [ ] All broker credentials are stored in Windows Credential Manager
      under a `trading_<broker>` service name.
- [ ] `python -m keyring get "trading_<broker>" "api_key"` returns the
      key (run this once in the adapter's venv to verify; clear the
      terminal afterwards).
- [ ] No credential appears anywhere in the main repo (grep the repo
      for any partial token; should match zero files).
- [ ] Backups, screen recordings, and screenshots taken today do not
      include any terminal showing credentials.

## Section D: Broker account

- [ ] The broker account is on the smallest available unit (FX:
      mini/micro lot; futures: smallest micro contract).
- [ ] The account balance equals an amount the operator can afford to
      fully lose. Record: $______ (USD or local currency).
- [ ] The broker UI is open in a browser or desktop app and the
      operator is logged in.
- [ ] The operator knows the broker's emergency-close hotline /
      contact form (record: ______).
- [ ] The broker is in cash / margin mode the operator understands
      (no surprise leverage, no surprise overnight rules).

## Section E: Env preconditions (the 9 LiveUnlockGate conditions)

For today's session, every one of these must be set, with the values
the operator owns:

- [ ] `LIVE_TRADING=true`
- [ ] `EXECUTION_MODE=live`
- [ ] `LIVE_READY_FLAG=approved-<today's UTC date as YYYYMMDD>`
- [ ] `LIVE_TOKEN_HMAC=<freshly generated random string, never reused>`
- [ ] `ALLOWED_SYMBOLS=<exactly ONE symbol for the burn-in period>`
- [ ] `MAX_DAILY_LOSS=<dollars, see Section F>`
- [ ] `MAX_POSITION_SIZE=1` (the smallest unit)
- [ ] `LIVE_BROKER_ADAPTER_PATH=C:\Trading\live-adapters\<broker>\live_adapter.py`
      OR `FAKE_LIVE_ADAPTER=true` (the latter for one more fake
      rehearsal before the real adapter)
- [ ] `logs/.killswitch` file does NOT currently exist
      (`Test-Path logs\.killswitch` returns `False`)

## Section F: Risk limits (the burn-in 10 trades)

For the first 10 real-money trades on this strategy:

- [ ] `MAX_POSITION_SIZE=1` (one unit, smallest available)
- [ ] `MAX_DAILY_TRADES=1` (one trade per UTC day, no exceptions)
- [ ] `MAX_LOSS_PER_TRADE` is at most 0.1% of account equity.
      Verify: 0.001 * $______ = $______
- [ ] `MAX_DAILY_LOSS` is at most 0.5% of account equity.
      Verify: 0.005 * $______ = $______
- [ ] `MIN_LIVE_CONFIDENCE=0.85` (higher than the 0.7 paper bar)
- [ ] Only one symbol allowed in `ALLOWED_SYMBOLS` for the burn-in.

After burn-in (trade 11+), the operator may relax to:
`MAX_DAILY_TRADES=3`, `MAX_LOSS_PER_TRADE=0.2%`, two symbols. Every
relaxation requires a fresh signing of this checklist.

**Hard caps that never relax** (even after burn-in):

- `MAX_DAILY_LOSS` <= 2% of equity
- `MIN_LIVE_CONFIDENCE` >= 0.7
- Any single trade with stop-distance > 1% of entry requires manual
  human approval (future ApprovalGate work; for now: refuse).

## Section G: Operator presence

- [ ] The operator is at the trading terminal, not in transit,
      meeting, or doing other distracting work.
- [ ] The operator has the broker UI open in another window.
- [ ] The operator has read `docs/emergency_stop.md` within the last
      24 hours.
- [ ] The operator has the kill-switch file path memorised:
      `C:\trading\ai_fibo_vision_trader\logs\.killswitch`.
- [ ] The operator has a fully charged phone with the broker's
      emergency contact number saved.

## Section H: Final review

- [ ] The operator has read the AI decision aloud before submitting.
- [ ] The operator has cross-checked entry, stop, target against the
      broker UI's current price (entry must be within 0.5% of current
      mid; otherwise re-evaluate).
- [ ] The operator agrees that, if this trade fully stops out, the
      loss equals the value computed in Section F.
- [ ] The operator commits to reviewing the trade post-fill
      (`docs/first_real_trade_procedure.md` section "T+post").

---

## Sign-off

I have completed every item in Sections A through H above. I take
responsibility for the trade about to be placed. If anything goes
wrong, I am the one who acts; the codebase is a tool, not an
operator.

| Field | Value |
|---|---|
| Operator name | ______________________ |
| Date (UTC) | __________________ |
| Symbol | __________________ |
| Side | __________________ |
| Decision ID | __________________ |
| Account equity at sign-off | $__________________ |
| MAX_LOSS_PER_TRADE configured | $__________________ |
| MAX_DAILY_LOSS configured | $__________________ |
| First burn-in trade? (yes/no) | __________________ |

Signature: ______________________

---

## If any box above is unticked

**STOP.** Do not proceed. The codebase cannot enforce these checks
for you -- it can only enforce the env preconditions (Section E) and
the per-order risk math (Sections F via MicroLiveGate). Sections A-D
and G-H are operator-only and silent if skipped.

Skipping any item is a decision to operate without that safety. That
is the operator's prerogative, but it must be a deliberate decision,
not an omission.
