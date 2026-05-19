"""Unit tests for ExecutorRouter (Phase 6.A-2)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from approval.models import ApprovalDecision  # noqa: E402
from executor.broker_adapter import MockBrokerAdapter  # noqa: E402
from executor.executor_router import (  # noqa: E402
    ExecutorRouter,
    LiveTradingForbidden,
    RoutedOutcome,
)
from executor.mock_executor import MockExecutor  # noqa: E402
from strategy.fibo_mob_v2 import Signal  # noqa: E402


def _signal(side: str = "LONG", **k) -> Signal:
    return Signal(
        side=side,
        entry=k.get("entry", 100.0),
        stop=k.get("stop", 99.0),
        target=k.get("target", 110.0),
        confidence=k.get("confidence", 0.8),
        reason=k.get("reason", "test"),
    )


def _approved(request_id: str = "req-1") -> ApprovalDecision:
    return ApprovalDecision(
        request_id=request_id,
        approved=True,
        reason="approved",
        operator_chat_id="12345",
        decided_ts=2.0,
        decided_timestamp="2026-05-19T00:00:02+00:00",
        elapsed_ms=1000.0,
    )


def _rejected(reason: str = "manual_reject", request_id: str = "req-1") -> ApprovalDecision:
    return ApprovalDecision(
        request_id=request_id,
        approved=False,
        reason=reason,
        operator_chat_id="12345" if reason != "approval_timeout" else None,
        decided_ts=2.0,
        decided_timestamp="2026-05-19T00:00:02+00:00",
        elapsed_ms=100.0,
    )


def _mock_router(tmp_path: Path, **k) -> ExecutorRouter:
    adapter = k.pop("adapter", None)
    if adapter is None and "execution_mode" not in k:
        adapter = MockBrokerAdapter(MockExecutor(log_path=str(tmp_path / "trades.jsonl")))
    return ExecutorRouter(
        adapter=adapter,
        log_path=str(tmp_path / "router.jsonl"),
        **k,
    )


def _read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------- construction / env ----------

def test_refuses_live_trading_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(log_path=str(tmp_path / "r.jsonl"))


def test_refuses_execution_mode_live(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_TRADING", "false")
    with pytest.raises(LiveTradingForbidden):
        ExecutorRouter(execution_mode="live", log_path=str(tmp_path / "r.jsonl"))


def test_resolves_to_mock_by_default(tmp_path):
    r = _mock_router(tmp_path)
    assert r.execution_mode == "mock"
    assert r.adapter_name == "mock"


def test_resolves_to_paper_when_mode_paper(tmp_path):
    r = ExecutorRouter(execution_mode="paper", log_path=str(tmp_path / "r.jsonl"))
    assert r.execution_mode == "paper"
    assert r.adapter_name == "paper"


def test_unknown_mode_falls_back_to_mock(tmp_path):
    r = ExecutorRouter(execution_mode="garbage", log_path=str(tmp_path / "r.jsonl"))
    assert r.execution_mode == "mock"
    assert r.adapter_name == "mock"


def test_uses_env_execution_mode_when_arg_omitted(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    r = ExecutorRouter(log_path=str(tmp_path / "r.jsonl"))
    assert r.execution_mode == "paper"


def test_log_path_default_is_logs_router_decisions_jsonl(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    r = ExecutorRouter(adapter=MockBrokerAdapter(
        MockExecutor(log_path=str(tmp_path / "t.jsonl"))
    ))
    assert r.log_path == Path("logs/router_decisions.jsonl")


def test_log_path_respects_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.jsonl"
    monkeypatch.setenv("ROUTER_DECISIONS_LOG", str(custom))
    r = ExecutorRouter(adapter=MockBrokerAdapter(
        MockExecutor(log_path=str(tmp_path / "t.jsonl"))
    ))
    assert r.log_path == Path(str(custom))


# ---------- route() rejected branches ----------

@pytest.mark.parametrize("reason,expected_skip", [
    ("manual_reject",         "approval_rejected"),
    ("approval_timeout",      "approval_timeout"),
    ("unauthorized_operator", "approval_unauthorized"),
])
def test_route_skips_when_approval_not_approved(tmp_path, reason, expected_skip):
    r = _mock_router(tmp_path)
    out = r.route(_signal(), _rejected(reason=reason), symbol="XAUUSD")
    assert out.submitted is False
    assert out.skip_reason == expected_skip
    rows = _read_jsonl(r.log_path)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "routed_outcome"
    assert rows[0]["submitted"] is False
    assert rows[0]["skip_reason"] == expected_skip
    assert rows[0]["mode"] == "mock"
    assert rows[0]["symbol"] == "XAUUSD"


def test_route_rejected_does_not_call_adapter(tmp_path):
    calls: list[Signal] = []

    class _Spy:
        name = "spy"

        def submit(self, signal):
            calls.append(signal)
            raise AssertionError("adapter must not be called on rejected approval")

    r = ExecutorRouter(adapter=_Spy(), log_path=str(tmp_path / "r.jsonl"))
    out = r.route(_signal(), _rejected())
    assert out.submitted is False
    assert calls == []


def test_route_rejected_does_not_write_trade_log(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    adapter = MockBrokerAdapter(MockExecutor(log_path=str(trade_log)))
    r = ExecutorRouter(adapter=adapter, log_path=str(tmp_path / "r.jsonl"))
    r.route(_signal(), _rejected(reason="manual_reject"))
    r.route(_signal(), _rejected(reason="approval_timeout"))
    r.route(_signal(), _rejected(reason="unauthorized_operator"))
    assert not trade_log.exists() or trade_log.read_text(encoding="utf-8").strip() == ""


# ---------- route() approved + mock ----------

def test_route_approved_mock_writes_trade_and_outcome(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    adapter = MockBrokerAdapter(MockExecutor(log_path=str(trade_log)))
    r = ExecutorRouter(adapter=adapter, log_path=str(tmp_path / "r.jsonl"))
    out = r.route(_signal(), _approved(), symbol="XAUUSD")
    assert out.submitted is True
    assert out.skip_reason is None
    assert out.adapter == "mock"
    assert out.execution_mode == "mock"
    assert out.fill_ts is not None
    rows = _read_jsonl(r.log_path)
    assert rows[-1]["submitted"] is True
    assert rows[-1]["adapter"] == "mock"
    assert rows[-1]["mode"] == "mock"
    trade_rows = _read_jsonl(trade_log)
    assert len(trade_rows) == 1
    assert trade_rows[0]["mode"] == "mock"
    assert trade_rows[0]["side"] == "LONG"


# ---------- route() approved + paper stub ----------

def test_route_approved_paper_stub_returns_submitted_false(tmp_path):
    r = ExecutorRouter(execution_mode="paper", log_path=str(tmp_path / "r.jsonl"))
    out = r.route(_signal(), _approved())
    assert out.submitted is False
    assert out.skip_reason == "paper_not_implemented"
    rows = _read_jsonl(r.log_path)
    assert rows[-1]["submitted"] is False
    assert rows[-1]["skip_reason"] == "paper_not_implemented"
    assert rows[-1]["adapter"] == "paper"


def test_route_approved_paper_stub_does_not_write_trade_log(tmp_path):
    """A paper-stub approval must NEVER pollute logs/trades.jsonl."""
    trade_log = tmp_path / "trades.jsonl"
    r = ExecutorRouter(execution_mode="paper", log_path=str(tmp_path / "r.jsonl"))
    # No adapter override; the in-repo PaperBrokerAdapter is the stub.
    r.route(_signal(), _approved())
    assert not trade_log.exists()


# ---------- log invariants ----------

def test_every_route_call_writes_exactly_one_row(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    adapter = MockBrokerAdapter(MockExecutor(log_path=str(trade_log)))
    r = ExecutorRouter(adapter=adapter, log_path=str(tmp_path / "r.jsonl"))
    r.route(_signal(), _approved(request_id="r1"), symbol="A")
    r.route(_signal("SHORT"), _approved(request_id="r2"), symbol="B")
    r.route(_signal(), _rejected(request_id="r3"), symbol="C")
    rows = _read_jsonl(r.log_path)
    assert len(rows) == 3
    assert [row["symbol"] for row in rows] == ["A", "B", "C"]
    assert [row["request_id"] for row in rows] == ["r1", "r2", "r3"]


def test_outcome_carries_request_id_from_approval(tmp_path):
    trade_log = tmp_path / "trades.jsonl"
    adapter = MockBrokerAdapter(MockExecutor(log_path=str(trade_log)))
    r = ExecutorRouter(adapter=adapter, log_path=str(tmp_path / "r.jsonl"))
    out = r.route(_signal(), _approved(request_id="req-abc"))
    assert out.request_id == "req-abc"
    rows = _read_jsonl(r.log_path)
    assert rows[-1]["request_id"] == "req-abc"


def test_routed_outcome_dataclass_is_frozen():
    out = RoutedOutcome(
        request_id="x", submitted=False, skip_reason="t",
        execution_mode="mock", adapter="mock",
        ts=1.0, timestamp="2026-05-19T00:00:00+00:00",
        symbol="X", side="LONG", fill_ts=None,
    )
    with pytest.raises(Exception):
        out.submitted = True  # type: ignore[misc]
