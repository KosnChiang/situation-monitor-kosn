"""Phase 6.A-4 tests for POST /webhook/tradingview.

Skipped if fastapi / httpx are not installed (clean-checkout safety,
mirroring tests/test_api.py)."""
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
os.environ["TRADES_LOG"] = str(ROOT / "logs" / "trades.test.jsonl")

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


client = TestClient(app)


def _read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [
        json.loads(line)
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _payload(**overrides) -> dict:
    base = {
        "secret": "test-secret-12345",
        "ticker": "XAUUSD",
        "side": "LONG",
        "price": 23010.5,
        "stop": 22980.0,
        "target": 23090.0,
        "confidence": 0.78,
        "reason": "tradingview-alert",
        "strategy_tag": "fibo-mob-v2",
    }
    base.update(overrides)
    return base


def _set_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_LOG", str(tmp_path / "webhooks.jsonl"))
    monkeypatch.setenv("ROUTER_DECISIONS_LOG", str(tmp_path / "router.jsonl"))
    monkeypatch.setenv("TRADES_LOG", str(tmp_path / "trades.jsonl"))
    monkeypatch.setenv("PAPER_TRADES_LOG", str(tmp_path / "paper_trades.jsonl"))
    monkeypatch.setenv("APPROVALS_LOG", str(tmp_path / "approvals.jsonl"))
    monkeypatch.delenv("TRADINGVIEW_REQUIRE_APPROVAL", raising=False)


# ---------- secret handling ----------

def test_disabled_when_secret_env_unset(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    r = client.post("/webhook/tradingview", json=_payload())
    assert r.status_code == 503
    rows = _read_jsonl(tmp_path / "webhooks.jsonl")
    assert rows
    assert rows[-1]["verdict"] == "disabled"
    assert rows[-1]["secret"] == "***"


def test_unauthorized_when_secret_mismatches(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-real-one")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="not-the-real-one"),
    )
    assert r.status_code == 401
    rows = _read_jsonl(tmp_path / "webhooks.jsonl")
    assert rows[-1]["verdict"] == "unauthorized"
    assert rows[-1]["secret"] == "***"
    assert "not-the-real-one" not in (tmp_path / "webhooks.jsonl").read_text("utf-8")


def test_payload_missing_secret_field_is_unprocessable(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "abc")
    bad = _payload()
    bad.pop("secret")
    r = client.post("/webhook/tradingview", json=bad)
    assert r.status_code == 422


def test_malformed_json_is_rejected(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "abc")
    r = client.post(
        "/webhook/tradingview",
        content=b"not json at all",
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (400, 422)


# ---------- mock path ----------

def test_correct_secret_long_mock_writes_trades_jsonl(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["outcome"]["adapter"] == "mock"
    assert body["outcome"]["execution_mode"] == "mock"

    trade_rows = _read_jsonl(tmp_path / "trades.jsonl")
    assert len(trade_rows) == 1
    assert trade_rows[0]["mode"] == "mock"
    assert trade_rows[0]["side"] == "LONG"

    webhook_rows = _read_jsonl(tmp_path / "webhooks.jsonl")
    assert webhook_rows[-1]["verdict"] == "routed"
    assert webhook_rows[-1]["secret"] == "***"


def test_correct_secret_flat_returns_409(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret", side="FLAT"),
    )
    assert r.status_code == 409
    assert not (tmp_path / "trades.jsonl").exists() \
        or (tmp_path / "trades.jsonl").read_text("utf-8").strip() == ""
    rows = _read_jsonl(tmp_path / "webhooks.jsonl")
    assert rows[-1]["verdict"] == "risk_rejected"


def test_low_confidence_returns_409(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    # risk_gate min_confidence default is 0.55
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret", confidence=0.20),
    )
    assert r.status_code == 409
    rows = _read_jsonl(tmp_path / "webhooks.jsonl")
    assert rows[-1]["verdict"] == "risk_rejected"
    assert not (tmp_path / "trades.jsonl").exists() \
        or (tmp_path / "trades.jsonl").read_text("utf-8").strip() == ""


# ---------- paper path ----------

def test_correct_secret_long_paper_writes_paper_trades_not_trades(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["outcome"]["adapter"] == "paper"
    assert body["outcome"]["execution_mode"] == "paper"

    paper_rows = _read_jsonl(tmp_path / "paper_trades.jsonl")
    assert len(paper_rows) == 1
    assert paper_rows[0]["event_type"] == "paper_open"
    assert paper_rows[0]["mode"] == "paper"

    assert not (tmp_path / "trades.jsonl").exists() \
        or (tmp_path / "trades.jsonl").read_text("utf-8").strip() == ""


# ---------- require_approval=true ----------

def test_require_approval_with_short_timeout_returns_skipped(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    monkeypatch.setenv("TRADINGVIEW_REQUIRE_APPROVAL", "true")
    monkeypatch.setenv("APPROVAL_TIMEOUT_SECONDS", "0.1")
    # No operator submits; ApprovalGate fires timeout.
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["outcome"]["skip_reason"] == "approval_timeout"
    assert not (tmp_path / "trades.jsonl").exists() \
        or (tmp_path / "trades.jsonl").read_text("utf-8").strip() == ""


# ---------- secret never logged ----------

def test_webhooks_jsonl_never_contains_real_secret(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "MY-VERY-SECRET-VALUE-DO-NOT-LOG")
    # Hit all four verdict paths: disabled is mutually exclusive with secret being set,
    # so we hit unauthorized, routed, risk_rejected here.
    client.post("/webhook/tradingview", json=_payload(secret="wrong"))
    client.post("/webhook/tradingview", json=_payload(secret="MY-VERY-SECRET-VALUE-DO-NOT-LOG"))
    client.post(
        "/webhook/tradingview",
        json=_payload(secret="MY-VERY-SECRET-VALUE-DO-NOT-LOG", side="FLAT"),
    )
    text = (tmp_path / "webhooks.jsonl").read_text("utf-8")
    assert "MY-VERY-SECRET-VALUE-DO-NOT-LOG" not in text
    assert "\"secret\": \"***\"" in text


# ---------- /health webhook field ----------

def test_health_reports_webhook_disabled_by_default(monkeypatch):
    monkeypatch.delenv("TRADINGVIEW_WEBHOOK_SECRET", raising=False)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["tradingview_webhook_enabled"] is False


def test_health_reports_webhook_enabled_when_secret_set(monkeypatch):
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "abc")
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["tradingview_webhook_enabled"] is True


# ---------- router_decisions.jsonl ----------

def test_routed_outcome_recorded_in_router_decisions_jsonl(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret"),
    )
    assert r.status_code == 200
    router_rows = _read_jsonl(tmp_path / "router.jsonl")
    assert router_rows
    assert router_rows[-1]["submitted"] is True
    assert router_rows[-1]["adapter"] == "mock"
    assert router_rows[-1]["symbol"] == "XAUUSD"


def test_short_side_is_accepted(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "the-secret")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="the-secret", side="SHORT", stop=23040.0, target=22920.0),
    )
    assert r.status_code == 200
    trade_rows = _read_jsonl(tmp_path / "trades.jsonl")
    assert trade_rows[-1]["side"] == "SHORT"


def test_invalid_side_value_is_rejected(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "abc")
    r = client.post(
        "/webhook/tradingview",
        json=_payload(secret="abc", side="LONGISH"),
    )
    assert r.status_code == 422
