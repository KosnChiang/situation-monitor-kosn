"""Unit tests for ApprovalGate (Phase 6.A-1, dry-run / mock-only)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from approval.approval_gate import ApprovalGate, LiveTradingForbidden  # noqa: E402


def _make_gate(tmp_path: Path, *, timeout: float = 1.0,
               allowed=("12345",)) -> ApprovalGate:
    return ApprovalGate(
        timeout_seconds=timeout,
        allowed_chat_ids=list(allowed),
        log_path=str(tmp_path / "approvals.jsonl"),
    )


def _read_log(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(line) for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sample_req(gate, **overrides):
    kwargs = dict(
        symbol="XAUUSD", side="LONG", entry=100.0, stop=99.0,
        target=110.0, confidence=0.8, reason="t",
    )
    kwargs.update(overrides)
    return gate.request(**kwargs)


# ---------- construction / safety ----------

def test_refuses_when_live_trading_env_flipped(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(LiveTradingForbidden):
        ApprovalGate(log_path=str(tmp_path / "x.jsonl"))


def test_accepts_when_live_trading_is_false(tmp_path):
    g = _make_gate(tmp_path)
    assert g.timeout_seconds == 1.0
    assert g.allowed_chat_ids == ["12345"]


def test_allowed_chat_ids_parsed_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEGRAM_APPROVE_CHAT_IDS", "111, 222 ,333")
    g = ApprovalGate(log_path=str(tmp_path / "x.jsonl"))
    assert g.allowed_chat_ids == ["111", "222", "333"]


def test_log_path_defaults_to_logs_approvals_jsonl(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    g = ApprovalGate(allowed_chat_ids=["1"])
    assert g.log_path == Path("logs/approvals.jsonl")


# ---------- request lifecycle ----------

def test_request_writes_pending_log(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    rows = _read_log(g.log_path)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "approval_request"
    assert rows[0]["request_id"] == req.request_id
    assert rows[0]["mode"] == "mock"
    assert rows[0]["side"] == "LONG"
    assert rows[0]["symbol"] == "XAUUSD"
    assert rows[0]["entry"] == 100.0


def test_approve_from_allowed_operator(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    d = g.submit_decision(req.request_id, approve=True, operator_chat_id="12345")
    assert d.approved is True
    assert d.reason == "approved"
    assert d.operator_chat_id == "12345"
    rows = _read_log(g.log_path)
    assert rows[-1]["event_type"] == "approval_decision"
    assert rows[-1]["reason"] == "approved"


def test_manual_reject_from_allowed_operator(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    d = g.submit_decision(req.request_id, approve=False, operator_chat_id="12345")
    assert d.approved is False
    assert d.reason == "manual_reject"
    rows = _read_log(g.log_path)
    assert rows[-1]["reason"] == "manual_reject"


def test_unauthorized_operator_is_rejected(tmp_path):
    g = _make_gate(tmp_path, allowed=("12345",))
    req = _sample_req(g)
    d = g.submit_decision(req.request_id, approve=True, operator_chat_id="99999")
    assert d.approved is False
    assert d.reason == "unauthorized_operator"
    assert d.operator_chat_id == "99999"
    rows = _read_log(g.log_path)
    assert rows[-1]["reason"] == "unauthorized_operator"
    assert rows[-1]["operator_chat_id"] == "99999"


def test_empty_operator_chat_id_is_unauthorized(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    d = g.submit_decision(req.request_id, approve=True, operator_chat_id="")
    assert d.approved is False
    assert d.reason == "unauthorized_operator"


# ---------- await_decision ----------

def test_await_decision_returns_existing_decision_immediately(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    g.submit_decision(req.request_id, approve=True, operator_chat_id="12345")
    t0 = time.monotonic()
    d = g.await_decision(req)
    elapsed = time.monotonic() - t0
    assert d.approved is True
    assert elapsed < 0.2


def test_await_decision_times_out_when_no_callback(tmp_path):
    g = _make_gate(tmp_path, timeout=0.1)
    req = _sample_req(g)
    t0 = time.monotonic()
    d = g.await_decision(req)
    elapsed = time.monotonic() - t0
    assert d.approved is False
    assert d.reason == "approval_timeout"
    assert d.operator_chat_id is None
    assert 0.05 < elapsed < 1.0
    rows = _read_log(g.log_path)
    assert rows[-1]["reason"] == "approval_timeout"


def test_await_decision_picks_up_late_callback(tmp_path):
    g = _make_gate(tmp_path, timeout=2.0)
    req = _sample_req(g)

    def _late() -> None:
        time.sleep(0.05)
        g.submit_decision(req.request_id, approve=True, operator_chat_id="12345")

    t = threading.Thread(target=_late, daemon=True)
    t.start()
    d = g.await_decision(req)
    t.join(timeout=1.0)
    assert d.approved is True
    assert d.reason == "approved"


# ---------- idempotency / errors ----------

def test_double_submit_is_idempotent_first_wins(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    d1 = g.submit_decision(req.request_id, approve=True, operator_chat_id="12345")
    d2 = g.submit_decision(req.request_id, approve=False, operator_chat_id="12345")
    assert d1.request_id == d2.request_id
    assert d2.approved is True
    assert d2.reason == "approved"
    rows = _read_log(g.log_path)
    decision_rows = [r for r in rows if r["event_type"] == "approval_decision"]
    assert len(decision_rows) == 1


def test_submit_for_unknown_request_id_raises(tmp_path):
    g = _make_gate(tmp_path)
    with pytest.raises(ValueError):
        g.submit_decision("bogus-id", approve=True, operator_chat_id="12345")


def test_log_records_contain_required_fields(tmp_path):
    g = _make_gate(tmp_path)
    req = _sample_req(g)
    g.submit_decision(req.request_id, approve=True, operator_chat_id="12345")
    rows = _read_log(g.log_path)
    pending = next(r for r in rows if r["event_type"] == "approval_request")
    final = next(r for r in rows if r["event_type"] == "approval_decision")
    for k in (
        "request_id", "ts", "timestamp", "symbol", "side", "entry",
        "stop", "target", "confidence", "qty", "reason", "mode",
        "timeout_seconds",
    ):
        assert k in pending, f"missing in pending: {k}"
    for k in (
        "request_id", "approved", "reason", "operator_chat_id",
        "decided_ts", "decided_timestamp", "elapsed_ms", "mode",
    ):
        assert k in final, f"missing in final: {k}"
    assert pending["mode"] == "mock"
    assert final["mode"] == "mock"


# ---------- CLI smoke ----------

def _run_cli(*args: str, log_path: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
    }
    return subprocess.run(
        [sys.executable, "-m", "tools.mock_approval_flow", *args,
         "--log-path", str(log_path)],
        cwd=str(ROOT), capture_output=True, text=True, env=env,
    )


def test_cli_approve(tmp_path):
    log = tmp_path / "approvals.jsonl"
    r = _run_cli("--action", "approve", "--chat-id", "12345",
                 "--allowed-ids", "12345", "--timeout", "1.5",
                 log_path=log)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rows = _read_log(log)
    assert any(
        row["event_type"] == "approval_decision" and row["reason"] == "approved"
        for row in rows
    )


def test_cli_reject(tmp_path):
    log = tmp_path / "approvals.jsonl"
    r = _run_cli("--action", "reject", "--chat-id", "12345",
                 "--allowed-ids", "12345", "--timeout", "1.5",
                 log_path=log)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rows = _read_log(log)
    assert any(row.get("reason") == "manual_reject" for row in rows)


def test_cli_timeout(tmp_path):
    log = tmp_path / "approvals.jsonl"
    r = _run_cli("--action", "timeout", "--timeout", "0.1",
                 log_path=log)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rows = _read_log(log)
    assert any(row.get("reason") == "approval_timeout" for row in rows)


def test_cli_unauthorized(tmp_path):
    log = tmp_path / "approvals.jsonl"
    r = _run_cli("--action", "unauthorized", "--chat-id", "99999",
                 "--allowed-ids", "12345", "--timeout", "1.5",
                 log_path=log)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rows = _read_log(log)
    assert any(row.get("reason") == "unauthorized_operator" for row in rows)
