"""End-to-end pipeline tests (Phase 6.A-2).

Wires Signal -> RiskGate -> ApprovalGate -> ExecutorRouter ->
MockBrokerAdapter -> MockExecutor and verifies the audit trail across
logs/approvals.jsonl + logs/router_decisions.jsonl + logs/trades.jsonl.

No webhook, no Telegram, no broker SDK. Mock-only.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from approval.approval_gate import ApprovalGate  # noqa: E402
from executor.broker_adapter import MockBrokerAdapter  # noqa: E402
from executor.executor_router import ExecutorRouter  # noqa: E402
from executor.mock_executor import MockExecutor  # noqa: E402
from risk.risk_gate import RiskGate  # noqa: E402
from strategy.fibo_mob_v2 import Signal  # noqa: E402


def _read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture
def pipeline(tmp_path):
    trades = tmp_path / "trades.jsonl"
    approvals = tmp_path / "approvals.jsonl"
    router_log = tmp_path / "router.jsonl"

    risk = RiskGate(min_confidence=0.6)
    gate = ApprovalGate(
        timeout_seconds=0.2,
        allowed_chat_ids=["12345"],
        log_path=str(approvals),
    )
    adapter = MockBrokerAdapter(MockExecutor(log_path=str(trades)))
    router = ExecutorRouter(adapter=adapter, log_path=str(router_log))

    return {
        "risk": risk,
        "gate": gate,
        "router": router,
        "trades": trades,
        "approvals": approvals,
        "router_log": router_log,
    }


def _drive(
    pipeline: dict,
    signal: Signal,
    *,
    approve: bool = True,
    operator_id: str = "12345",
    submit_decision: bool = True,
    symbol: str = "XAUUSD",
):
    risk = pipeline["risk"]
    gate = pipeline["gate"]
    router = pipeline["router"]

    risk_decision = risk.check(signal)
    if not risk_decision.approved:
        return None, risk_decision, None

    req = gate.request(
        symbol=symbol,
        side=signal.side,
        entry=signal.entry,
        stop=signal.stop,
        target=signal.target,
        confidence=signal.confidence,
        reason=signal.reason,
    )

    if submit_decision:
        def _submit() -> None:
            time.sleep(0.01)
            gate.submit_decision(
                req.request_id, approve=approve, operator_chat_id=operator_id
            )

        threading.Thread(target=_submit, daemon=True).start()

    decision = gate.await_decision(req)
    outcome = router.route(signal, decision, symbol=symbol)
    return req, risk_decision, outcome


def test_happy_path_writes_trade_and_outcome(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "happy")
    req, risk_decision, outcome = _drive(pipeline, sig, approve=True)
    assert risk_decision.approved is True
    assert outcome is not None
    assert outcome.submitted is True
    assert outcome.skip_reason is None
    trades = _read_jsonl(pipeline["trades"])
    assert len(trades) == 1
    assert trades[0]["mode"] == "mock"
    assert trades[0]["side"] == "LONG"
    router_rows = _read_jsonl(pipeline["router_log"])
    assert router_rows[-1]["submitted"] is True
    assert router_rows[-1]["adapter"] == "mock"
    assert router_rows[-1]["execution_mode"] == "mock"


def test_risk_gate_rejects_low_confidence_skips_pipeline(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.3, "low conf")
    req, risk_decision, outcome = _drive(pipeline, sig)
    assert risk_decision.approved is False
    assert outcome is None
    assert _read_jsonl(pipeline["trades"]) == []
    assert _read_jsonl(pipeline["router_log"]) == []
    assert _read_jsonl(pipeline["approvals"]) == []


def test_risk_gate_rejects_flat_skips_pipeline(pipeline):
    sig = Signal("FLAT", 100.0, 100.0, 100.0, 0.9, "flat")
    req, risk_decision, outcome = _drive(pipeline, sig)
    assert risk_decision.approved is False
    assert outcome is None
    assert _read_jsonl(pipeline["trades"]) == []
    assert _read_jsonl(pipeline["router_log"]) == []


def test_approval_rejected_does_not_submit_trade(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "rej")
    req, risk_decision, outcome = _drive(pipeline, sig, approve=False)
    assert outcome.submitted is False
    assert outcome.skip_reason == "approval_rejected"
    assert _read_jsonl(pipeline["trades"]) == []
    router_rows = _read_jsonl(pipeline["router_log"])
    assert router_rows[-1]["skip_reason"] == "approval_rejected"


def test_approval_timeout_does_not_submit_trade(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "timeout")
    req, risk_decision, outcome = _drive(pipeline, sig, submit_decision=False)
    assert outcome.submitted is False
    assert outcome.skip_reason == "approval_timeout"
    assert _read_jsonl(pipeline["trades"]) == []
    router_rows = _read_jsonl(pipeline["router_log"])
    assert router_rows[-1]["skip_reason"] == "approval_timeout"


def test_unauthorized_operator_does_not_submit_trade(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "unauth")
    req, risk_decision, outcome = _drive(pipeline, sig, operator_id="99999")
    assert outcome.submitted is False
    assert outcome.skip_reason == "approval_unauthorized"
    assert _read_jsonl(pipeline["trades"]) == []
    router_rows = _read_jsonl(pipeline["router_log"])
    assert router_rows[-1]["skip_reason"] == "approval_unauthorized"


def test_three_log_files_share_request_id(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "trace")
    req, _, _ = _drive(pipeline, sig)
    approvals = _read_jsonl(pipeline["approvals"])
    router_rows = _read_jsonl(pipeline["router_log"])
    approval_req_ids = {r["request_id"] for r in approvals}
    router_req_ids = {r["request_id"] for r in router_rows}
    assert req.request_id in approval_req_ids
    assert req.request_id in router_req_ids


def test_consecutive_signals_each_get_unique_request_id(pipeline):
    sig1 = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "s1")
    sig2 = Signal("SHORT", 100.0, 101.0, 90.0, 0.8, "s2")
    req1, _, out1 = _drive(pipeline, sig1, approve=True)
    req2, _, out2 = _drive(pipeline, sig2, approve=True)
    assert req1.request_id != req2.request_id
    assert out1.request_id != out2.request_id
    trades = _read_jsonl(pipeline["trades"])
    assert len(trades) == 2
    router_rows = _read_jsonl(pipeline["router_log"])
    assert len(router_rows) == 2
