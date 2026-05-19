"""Webhook /webhook/ai-swing tests (Phase 7.A)."""
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


def _alert(**overrides) -> dict:
    base = {
        "secret": "the-secret",
        "source": "tradingview",
        "strategy": "twtx_fibo_v4",
        "symbol": "TMFR1",
        "timeframe": "5",
        "bar_time": "2026-05-20T14:55:00Z",
        "open":  23015.0, "high": 23022.0, "low": 22995.0, "close": 23018.0,
        "prev_ohlc": {"open": 23000.0, "high": 23080.0, "low": 22970.0, "close": 23055.0},
        "fibo": {
            "base": 23026.25, "range": 110.0,
            "nearest_ratio": 0.382, "nearest_price": 23068.27,
            "touch_support": True, "touch_resistance": False,
            "zone": "support",
        },
        "rsi": {
            "value": 28.5, "ma": 42.1, "state": "oversold",
            "bull_div": True, "bear_div": False,
        },
        "pivot": {"last_high": 23110.0, "last_low": 22960.0},
        "signal": {
            "buy": False, "sell": False,
            "combo_buy": True, "combo_sell": False,
            "div_buy": True, "div_sell": False,
            "touch_fibo": True,
        },
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in base:
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base


def _set_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("CHART_CONTEXT_LOG", str(tmp_path / "chart_context.jsonl"))
    monkeypatch.setenv("AI_SWING_DECISIONS_LOG", str(tmp_path / "ai_swing_decisions.jsonl"))
    monkeypatch.setenv("AI_SWING_WEBHOOK_LOG", str(tmp_path / "ai_swing_webhook.jsonl"))
    monkeypatch.setenv("LIVE_ORDERS_LOG", str(tmp_path / "live_orders.jsonl"))
    monkeypatch.setenv("LIVE_FILLS_LOG", str(tmp_path / "live_fills.jsonl"))
    monkeypatch.setenv("LIVE_REJECTIONS_LOG", str(tmp_path / "live_rejections.jsonl"))
    monkeypatch.setenv("AI_SWING_POSITION_STATE", str(tmp_path / "position.json"))
    monkeypatch.setenv("AI_SWING_COOLDOWN_STATE", str(tmp_path / "cooldown.json"))


def _read_jsonl(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


# ---------- secret handling ----------

def test_disabled_when_secret_env_unset(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("AI_SWING_WEBHOOK_SECRET", raising=False)
    r = client.post("/webhook/ai-swing", json=_alert())
    assert r.status_code == 503
    rows = _read_jsonl(tmp_path / "ai_swing_webhook.jsonl")
    assert rows and rows[-1]["verdict"] == "disabled"
    assert rows[-1]["payload"]["secret"] == "***"


def test_unauthorized_when_secret_wrong(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SWING_WEBHOOK_SECRET", "real-secret")
    r = client.post("/webhook/ai-swing", json=_alert(secret="wrong"))
    assert r.status_code == 401
    rows = _read_jsonl(tmp_path / "ai_swing_webhook.jsonl")
    assert rows[-1]["verdict"] == "unauthorized"
    text = (tmp_path / "ai_swing_webhook.jsonl").read_text("utf-8")
    assert "wrong" not in text  # redacted


def test_missing_secret_field_unprocessable(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SWING_WEBHOOK_SECRET", "abc")
    bad = _alert()
    bad.pop("secret")
    r = client.post("/webhook/ai-swing", json=bad)
    assert r.status_code == 422


# ---------- happy path: ENTRY ----------

def test_combo_buy_setup_yields_entry_long(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SWING_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("AI_SWING_AUTO_SUBMIT", "true")
    r = client.post("/webhook/ai-swing", json=_alert(secret="secret"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["action"] == "ENTRY"
    assert body["side"] == "LONG"
    assert body["submitted"] is True
    assert body["order_id"] is not None
    # chart_context.jsonl, ai_swing_decisions.jsonl populated
    assert _read_jsonl(tmp_path / "chart_context.jsonl")
    decs = _read_jsonl(tmp_path / "ai_swing_decisions.jsonl")
    assert decs[-1]["action"] == "ENTRY"
    # live_orders.jsonl + live_fills.jsonl populated (via fake adapter)
    orders = _read_jsonl(tmp_path / "live_orders.jsonl")
    fills = _read_jsonl(tmp_path / "live_fills.jsonl")
    assert orders and fills
    assert orders[-1]["order_id"] == fills[-1]["order_id"] == body["order_id"]
    # position state persisted
    pos = json.loads((tmp_path / "position.json").read_text("utf-8"))
    assert pos["side"] == "LONG"


# ---------- happy path: NO_TRADE ----------

def test_no_signal_yields_no_trade(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SWING_WEBHOOK_SECRET", "secret")
    alert = _alert(
        secret="secret",
        fibo={"touch_support": False},
        rsi={"state": "neutral"},
        signal={"combo_buy": False},
    )
    r = client.post("/webhook/ai-swing", json=alert)
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "NO_TRADE"
    assert body["submitted"] is False
    # no order / fill written
    assert not (tmp_path / "live_orders.jsonl").exists() \
        or (tmp_path / "live_orders.jsonl").read_text("utf-8").strip() == ""


def test_auto_submit_disabled_skips_dispatch(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("AI_SWING_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("AI_SWING_AUTO_SUBMIT", "false")
    r = client.post("/webhook/ai-swing", json=_alert(secret="secret"))
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == "ENTRY"
    assert body["submitted"] is False
    assert body["order_id"] is None


# ---------- secret leak guard ----------

def test_webhook_log_never_contains_real_secret(monkeypatch, tmp_path):
    _set_paths(monkeypatch, tmp_path)
    real_secret = "MY-VERY-SECRET-VALUE-AI-SWING-DO-NOT-LOG"
    monkeypatch.setenv("AI_SWING_WEBHOOK_SECRET", real_secret)
    client.post("/webhook/ai-swing", json=_alert(secret=real_secret))
    text = (tmp_path / "ai_swing_webhook.jsonl").read_text("utf-8")
    assert real_secret not in text
    assert '"secret": "***"' in text or '"secret":"***"' in text
