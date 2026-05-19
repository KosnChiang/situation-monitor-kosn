"""End-to-end pipeline tests for EXECUTION_MODE=paper (Phase 6.A-3).

Wires Signal -> RiskGate -> ApprovalGate -> ExecutorRouter ->
PaperBrokerAdapter -> PaperExecutor. Verifies:

  * approved + paper writes to paper_trades.jsonl (mode=paper)
  * approved + paper NEVER writes to trades.jsonl
  * tick() closes positions via stop / target
  * reject / timeout / unauthorized do not produce any paper row
  * router records adapter="paper", submitted=true
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
os.environ["EXECUTION_MODE"] = "mock"  # module-level; per-test router overrides

from approval.approval_gate import ApprovalGate  # noqa: E402
from executor.broker_adapter import PaperBrokerAdapter  # noqa: E402
from executor.executor_router import ExecutorRouter  # noqa: E402
from paper.paper_executor import PaperExecutor  # noqa: E402
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
    paper_log = tmp_path / "paper_trades.jsonl"
    trades_log = tmp_path / "trades.jsonl"          # MUST NOT be created
    approvals = tmp_path / "approvals.jsonl"
    router_log = tmp_path / "router.jsonl"

    risk = RiskGate(min_confidence=0.6)
    gate = ApprovalGate(
        timeout_seconds=0.2,
        allowed_chat_ids=["12345"],
        log_path=str(approvals),
    )
    pexec = PaperExecutor(log_path=str(paper_log))
    adapter = PaperBrokerAdapter(executor=pexec)
    router = ExecutorRouter(
        adapter=adapter,
        execution_mode="paper",
        log_path=str(router_log),
    )
    return {
        "risk": risk, "gate": gate, "router": router, "paper": pexec,
        "paper_log": paper_log, "trades_log": trades_log,
        "approvals": approvals, "router_log": router_log,
    }


def _drive(pipeline, signal, *, approve=True, operator_id="12345", submit_decision=True):
    risk_decision = pipeline["risk"].check(signal)
    if not risk_decision.approved:
        return None, risk_decision, None
    req = pipeline["gate"].request(
        symbol="XAUUSD",
        side=signal.side, entry=signal.entry, stop=signal.stop,
        target=signal.target, confidence=signal.confidence, reason=signal.reason,
    )
    if submit_decision:
        def _submit() -> None:
            time.sleep(0.01)
            pipeline["gate"].submit_decision(
                req.request_id, approve=approve, operator_chat_id=operator_id
            )
        threading.Thread(target=_submit, daemon=True).start()
    decision = pipeline["gate"].await_decision(req)
    outcome = pipeline["router"].route(signal, decision, symbol="XAUUSD")
    return req, risk_decision, outcome


def test_happy_path_paper_open_written_no_trades_jsonl(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "happy")
    _, _, outcome = _drive(pipeline, sig)
    assert outcome.submitted is True
    assert outcome.adapter == "paper"
    assert outcome.execution_mode == "paper"
    paper_rows = _read_jsonl(pipeline["paper_log"])
    assert len(paper_rows) == 1
    assert paper_rows[0]["event_type"] == "paper_open"
    assert paper_rows[0]["mode"] == "paper"
    assert not pipeline["trades_log"].exists()


def test_router_marks_submitted_true_for_paper(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "submitted")
    _, _, outcome = _drive(pipeline, sig)
    assert outcome.submitted is True
    assert outcome.skip_reason is None
    router_rows = _read_jsonl(pipeline["router_log"])
    assert router_rows[-1]["adapter"] == "paper"
    assert router_rows[-1]["submitted"] is True


def test_stop_hit_via_tick_closes_position(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "tick-stop")
    _drive(pipeline, sig)
    closed = pipeline["paper"].tick({"last": 98.0})
    assert len(closed) == 1
    assert closed[0].close_reason == "stopped"
    paper_rows = _read_jsonl(pipeline["paper_log"])
    close_rows = [r for r in paper_rows if r["event_type"] == "paper_close"]
    assert len(close_rows) == 1
    assert close_rows[0]["close_reason"] == "stopped"
    assert close_rows[0]["mode"] == "paper"
    assert not pipeline["trades_log"].exists()


def test_target_hit_via_tick_closes_position(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "tick-target")
    _drive(pipeline, sig)
    closed = pipeline["paper"].tick({"last": 111.0})
    assert closed[0].close_reason == "targeted"
    paper_rows = _read_jsonl(pipeline["paper_log"])
    close_rows = [r for r in paper_rows if r["event_type"] == "paper_close"]
    assert close_rows[0]["close_reason"] == "targeted"
    assert close_rows[0]["mode"] == "paper"


def test_approval_rejected_no_paper_row_written(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "rej")
    _, _, outcome = _drive(pipeline, sig, approve=False)
    assert outcome.submitted is False
    assert outcome.skip_reason == "approval_rejected"
    assert _read_jsonl(pipeline["paper_log"]) == []
    assert not pipeline["trades_log"].exists()


def test_approval_timeout_no_paper_row_written(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "tmo")
    _, _, outcome = _drive(pipeline, sig, submit_decision=False)
    assert outcome.submitted is False
    assert outcome.skip_reason == "approval_timeout"
    assert _read_jsonl(pipeline["paper_log"]) == []


def test_unauthorized_operator_no_paper_row_written(pipeline):
    sig = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "unauth")
    _, _, outcome = _drive(pipeline, sig, operator_id="99999")
    assert outcome.submitted is False
    assert outcome.skip_reason == "approval_unauthorized"
    assert _read_jsonl(pipeline["paper_log"]) == []


def test_consecutive_paper_trades_each_get_unique_position_id(pipeline):
    s1 = Signal("LONG", 100.0, 99.0, 110.0, 0.8, "s1")
    s2 = Signal("SHORT", 100.0, 101.0, 90.0, 0.8, "s2")
    _drive(pipeline, s1)
    _drive(pipeline, s2)
    paper_rows = _read_jsonl(pipeline["paper_log"])
    open_rows = [r for r in paper_rows if r["event_type"] == "paper_open"]
    assert len(open_rows) == 2
    assert open_rows[0]["position_id"] != open_rows[1]["position_id"]
