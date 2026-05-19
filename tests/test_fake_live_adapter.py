"""Unit tests for live.fake_live_adapter.FakeLiveBrokerAdapter (Phase 6.B-1)."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from live.broker_adapter_protocol import LiveBrokerAdapterProtocol  # noqa: E402
from live.fake_live_adapter import FakeLiveBrokerAdapter  # noqa: E402
from live.models import LiveFill, LiveOrder  # noqa: E402


def _order(**overrides) -> LiveOrder:
    base = dict(
        order_id="ord-1",
        ts=time.time(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        decision_id="ai-1",
        symbol="XAUUSD",
        side="LONG",
        qty=1.0,
        entry=23010.5,
        stop=22995.0,
        target=23040.0,
        confidence=0.85,
        reason="test",
    )
    base.update(overrides)
    return LiveOrder(**base)


def _adapter(tmp_path: Path, **kw) -> FakeLiveBrokerAdapter:
    return FakeLiveBrokerAdapter(
        order_log_path=str(tmp_path / "live_orders.jsonl"),
        fill_log_path=str(tmp_path / "live_fills.jsonl"),
        rejection_log_path=str(tmp_path / "live_rejections.jsonl"),
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


def test_name_is_fake_live(tmp_path):
    a = _adapter(tmp_path)
    assert a.name == "fake-live"


def test_satisfies_protocol(tmp_path):
    a = _adapter(tmp_path)
    assert isinstance(a, LiveBrokerAdapterProtocol)


def test_submit_order_writes_order_and_fill(tmp_path):
    a = _adapter(tmp_path)
    a.connect()
    order = _order()
    fill = a.submit_order(order)
    assert isinstance(fill, LiveFill)
    assert fill.status == "filled"
    assert fill.fill_price == order.entry
    assert fill.slippage == 0.0
    assert fill.order_id == order.order_id

    orders = _read_jsonl(a.order_log_path)
    fills = _read_jsonl(a.fill_log_path)
    assert len(orders) == 1
    assert orders[0]["event_type"] == "live_order"
    assert orders[0]["mode"] == "live"
    assert len(fills) == 1
    assert fills[0]["event_type"] == "live_fill"
    assert fills[0]["mode"] == "live"
    assert fills[0]["order_id"] == order.order_id


def test_submit_order_requires_connect(tmp_path):
    a = _adapter(tmp_path)
    with pytest.raises(RuntimeError):
        a.submit_order(_order())


def test_disconnect_blocks_further_submissions(tmp_path):
    a = _adapter(tmp_path)
    a.connect()
    a.submit_order(_order())
    a.disconnect()
    with pytest.raises(RuntimeError):
        a.submit_order(_order(order_id="ord-2"))


def test_cancel_order_returns_false_in_v1(tmp_path):
    a = _adapter(tmp_path)
    a.connect()
    assert a.cancel_order("any-id") is False


def test_positions_returns_empty_list(tmp_path):
    a = _adapter(tmp_path)
    a.connect()
    a.submit_order(_order())
    assert a.positions() == []


def test_account_equity_default_or_env(tmp_path, monkeypatch):
    monkeypatch.delenv("FAKE_LIVE_EQUITY", raising=False)
    a = _adapter(tmp_path)
    assert a.account_equity() == 10000.0
    a2 = _adapter(tmp_path, account_equity=5000.0)
    assert a2.account_equity() == 5000.0
    monkeypatch.setenv("FAKE_LIVE_EQUITY", "7777")
    a3 = _adapter(tmp_path)
    assert a3.account_equity() == 7777.0


def test_order_and_fill_share_order_id(tmp_path):
    a = _adapter(tmp_path)
    a.connect()
    o = _order(order_id="link-test")
    a.submit_order(o)
    orders = _read_jsonl(a.order_log_path)
    fills = _read_jsonl(a.fill_log_path)
    assert orders[0]["order_id"] == fills[0]["order_id"] == "link-test"
