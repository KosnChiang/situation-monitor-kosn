"""Unit tests for live.pipeline.LivePipeline (Phase 6.B-2)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from live.fake_live_adapter import FakeLiveBrokerAdapter  # noqa: E402
from live.kill_switch import KillSwitch  # noqa: E402
from live.live_unlock_gate import LiveUnlockGate  # noqa: E402
from live.micro_live_gate import MicroLiveGate  # noqa: E402
from live.pipeline import LivePipeline, PipelineResult  # noqa: E402


def _today_flag() -> str:
    return f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def _set_unlock_env(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("LIVE_READY_FLAG", _today_flag())
    monkeypatch.setenv("LIVE_TOKEN_HMAC", "test-hmac-1234")
    monkeypatch.setenv("ALLOWED_SYMBOLS", "XAUUSD,EURUSD")
    monkeypatch.setenv("MAX_DAILY_LOSS", "50")
    monkeypatch.setenv("MAX_POSITION_SIZE", "1")
    monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")
    monkeypatch.delenv("LIVE_BROKER_ADAPTER_PATH", raising=False)
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)


def _decision_payload(**overrides) -> dict:
    base = {
        "decision_id": "ai-test-1",
        "symbol": "XAUUSD",
        "side": "LONG",
        "entry": 23010.5,
        "stop": 22995.0,
        "target": 23040.0,
        "confidence": 0.85,
        "reason": "fibo MOB",
        "invalidation": "below stop",
        "data_sources": ["fibo_lines_filtered.json"],
        "mode": "live",
    }
    base.update(overrides)
    return base


def _build_pipeline(tmp_path: Path, **kw) -> LivePipeline:
    adapter = FakeLiveBrokerAdapter(
        order_log_path=str(tmp_path / "live_orders.jsonl"),
        fill_log_path=str(tmp_path / "live_fills.jsonl"),
        rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
    )
    return LivePipeline(
        adapter=adapter,
        kill_switch=KillSwitch(kill_file_path=str(tmp_path / ".killswitch")),
        unlock_gate=LiveUnlockGate(kill_file_path=str(tmp_path / ".killswitch")),
        micro_gate=MicroLiveGate(
            allowed_symbols=kw.pop("allowed_symbols", ["XAUUSD", "EURUSD"]),
            max_position_size=kw.pop("max_position_size", 1.0),
            max_daily_trades=kw.pop("max_daily_trades", 3),
            max_loss_per_trade=kw.pop("max_loss_per_trade", 30.0),
            max_daily_loss=kw.pop("max_daily_loss", 50.0),
            min_live_confidence=kw.pop("min_live_confidence", 0.7),
            rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
        ),
        ai_decisions_log=str(tmp_path / "ai_decisions.jsonl"),
        rejections_log=str(tmp_path / "live_rejections.jsonl"),
        **kw,
    )


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------- exit 0: happy path ----------

def test_happy_path_writes_order_fill_and_decision(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    result = p.run(_decision_payload(), qty=1.0)
    assert result.exit_code == 0
    assert result.outcome == "filled"
    assert result.fill is not None
    assert result.order_id is not None
    assert result.decision_id == "ai-test-1"

    orders = _read_jsonl(tmp_path / "live_orders.jsonl")
    fills = _read_jsonl(tmp_path / "live_fills.jsonl")
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    decisions = _read_jsonl(tmp_path / "ai_decisions.jsonl")

    assert len(orders) == 1
    assert len(fills) == 1
    assert orders[0]["mode"] == "live"
    assert fills[0]["mode"] == "live"
    assert orders[0]["order_id"] == fills[0]["order_id"] == result.order_id
    assert orders[0]["decision_id"] == "ai-test-1"
    assert rejections == []
    assert decisions and decisions[-1]["valid"] is True


# ---------- exit 2: ai_decision invalid ----------

def test_invalid_ai_decision_returns_exit_2(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    result = p.run("not json", qty=1.0)
    assert result.exit_code == 2
    assert result.outcome == "ai_invalid"
    assert result.decision is None
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r["rejection_layer"] == "ai_decision_validator" for r in rejections)
    assert not (tmp_path / "live_orders.jsonl").exists() \
        or (tmp_path / "live_orders.jsonl").read_text("utf-8").strip() == ""


def test_invalid_ai_decision_missing_side(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    payload = _decision_payload()
    payload.pop("side")
    result = p.run(payload, qty=1.0)
    assert result.exit_code == 2
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any("ai_decision_validator" == r["rejection_layer"] for r in rejections)


# ---------- exit 3: LiveUnlockGate refused ----------

def test_unlock_refused_returns_exit_3(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    monkeypatch.delenv("MAX_DAILY_LOSS", raising=False)
    p = _build_pipeline(tmp_path)
    result = p.run(_decision_payload(), qty=1.0)
    assert result.exit_code == 3
    assert result.outcome == "unlock_refused"
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r["rejection_layer"] == "live_unlock_gate" for r in rejections)
    assert "MAX_DAILY_LOSS_empty" in rejections[-1]["reason"]


# ---------- exit 4: KillSwitch active ----------

def test_killswitch_file_returns_exit_4(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    # unlock_gate's kill_file check happens first; bypass by giving
    # unlock_gate a different kill_file path than kill_switch.
    p = LivePipeline(
        adapter=FakeLiveBrokerAdapter(
            order_log_path=str(tmp_path / "live_orders.jsonl"),
            fill_log_path=str(tmp_path / "live_fills.jsonl"),
            rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
        ),
        unlock_gate=LiveUnlockGate(kill_file_path=str(tmp_path / ".no_kill")),
        kill_switch=KillSwitch(kill_file_path=str(tmp_path / ".kill_runtime")),
        micro_gate=MicroLiveGate(
            allowed_symbols=["XAUUSD"],
            rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
        ),
        ai_decisions_log=str(tmp_path / "ai_decisions.jsonl"),
        rejections_log=str(tmp_path / "live_rejections.jsonl"),
    )
    (tmp_path / ".kill_runtime").write_text("trip", encoding="utf-8")
    result = p.run(_decision_payload(), qty=1.0)
    assert result.exit_code == 4
    assert result.outcome == "killed"
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r["rejection_layer"] == "kill_switch" for r in rejections)


def test_killswitch_env_returns_exit_4(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    monkeypatch.setenv("LIVE_KILL_SWITCH", "1")
    p = _build_pipeline(tmp_path)
    result = p.run(_decision_payload(), qty=1.0)
    assert result.exit_code == 4
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    last = rejections[-1]
    assert last["rejection_layer"] == "kill_switch"
    assert "env_kill_switch" in last["reason"]


def test_killswitch_in_process_trip_returns_exit_4(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    p.kill_switch.trip("test_in_process")
    result = p.run(_decision_payload(), qty=1.0)
    assert result.exit_code == 4


# ---------- exit 7: not tradable ----------

def test_flat_side_returns_exit_7(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    result = p.run(_decision_payload(side="FLAT"), qty=1.0)
    assert result.exit_code == 7
    assert result.outcome == "not_tradable"
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r["rejection_layer"] == "not_tradable" for r in rejections)


def test_watch_side_returns_exit_7(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    result = p.run(_decision_payload(side="WATCH"), qty=1.0)
    assert result.exit_code == 7


def test_low_confidence_returns_exit_7(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path, min_live_confidence=0.5)
    # validator threshold = 0.7 default => below threshold tradable=False
    result = p.run(_decision_payload(confidence=0.5), qty=1.0)
    assert result.exit_code == 7


# ---------- exit 5: MicroLiveGate rejected ----------

def test_micro_rejects_oversize_qty_returns_exit_5(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path, max_position_size=1.0)
    result = p.run(_decision_payload(), qty=99.0)
    assert result.exit_code == 5
    assert result.outcome == "micro_rejected"
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r["rejection_layer"] == "micro_live_gate" for r in rejections)


def test_micro_rejects_symbol_not_allowed(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path, allowed_symbols=["EURUSD"])
    result = p.run(_decision_payload(symbol="XAUUSD"), qty=1.0)
    assert result.exit_code == 5


def test_micro_rejects_daily_trade_cap(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path, max_daily_trades=3)
    result = p.run(_decision_payload(), qty=1.0, daily_trade_count=3)
    assert result.exit_code == 5


# ---------- exit 6: adapter error ----------

def test_adapter_raises_returns_exit_6(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)

    class _BoomAdapter(FakeLiveBrokerAdapter):
        def submit_order(self, order):  # noqa: ARG002
            raise RuntimeError("simulated broker outage")

    adapter = _BoomAdapter(
        order_log_path=str(tmp_path / "live_orders.jsonl"),
        fill_log_path=str(tmp_path / "live_fills.jsonl"),
        rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
    )
    p = LivePipeline(
        adapter=adapter,
        kill_switch=KillSwitch(kill_file_path=str(tmp_path / ".killswitch")),
        unlock_gate=LiveUnlockGate(kill_file_path=str(tmp_path / ".killswitch")),
        micro_gate=MicroLiveGate(
            allowed_symbols=["XAUUSD"],
            max_loss_per_trade=100.0,
            max_daily_loss=200.0,
            rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
        ),
        ai_decisions_log=str(tmp_path / "ai_decisions.jsonl"),
        rejections_log=str(tmp_path / "live_rejections.jsonl"),
    )
    result = p.run(_decision_payload(), qty=1.0)
    assert result.exit_code == 6
    assert result.outcome == "adapter_error"
    assert result.order_id is not None
    rejections = _read_jsonl(tmp_path / "live_rejections.jsonl")
    last = rejections[-1]
    assert last["rejection_layer"] == "adapter"
    assert "simulated broker outage" in last["reason"]
    assert last["order_id"] == result.order_id


# ---------- audit join sanity ----------

def test_decision_id_links_all_four_logs_on_happy_path(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    p.run(_decision_payload(decision_id="join-test"), qty=1.0)
    orders = _read_jsonl(tmp_path / "live_orders.jsonl")
    fills = _read_jsonl(tmp_path / "live_fills.jsonl")
    decisions = _read_jsonl(tmp_path / "ai_decisions.jsonl")
    assert orders[0]["decision_id"] == "join-test"
    assert orders[0]["order_id"] == fills[0]["order_id"]
    assert decisions[-1]["decision"]["decision_id"] == "join-test"


def test_pipeline_run_twice_yields_unique_order_ids(monkeypatch, tmp_path):
    _set_unlock_env(monkeypatch)
    p = _build_pipeline(tmp_path)
    r1 = p.run(_decision_payload(decision_id="d1"), qty=1.0)
    r2 = p.run(_decision_payload(decision_id="d2"), qty=1.0)
    assert r1.order_id != r2.order_id
    orders = _read_jsonl(tmp_path / "live_orders.jsonl")
    assert len(orders) == 2
    assert orders[0]["decision_id"] == "d1"
    assert orders[1]["decision_id"] == "d2"


def test_pipeline_requires_adapter():
    with pytest.raises((TypeError, ValueError)):
        LivePipeline(adapter=None)  # type: ignore[arg-type]
