"""Live audit log integration + schema tests (Phase 6.B-1)."""
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

from live.audit_log import (  # noqa: E402
    default_fill_log_path,
    default_order_log_path,
    default_rejection_log_path,
    write_live_fill,
    write_live_order,
    write_live_rejection,
)
from live.models import LiveFill, LiveOrder, LiveRejection  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _now():
    return time.time(), datetime.now(timezone.utc).isoformat()


def test_live_order_log_schema_includes_event_type_and_mode(tmp_path):
    ts, iso = _now()
    log = tmp_path / "orders.jsonl"
    order = LiveOrder(
        order_id="o-1", ts=ts, timestamp=iso, decision_id="d-1",
        symbol="XAUUSD", side="LONG", qty=1.0, entry=23010.0,
        stop=22995.0, target=23040.0, confidence=0.85, reason="t",
    )
    write_live_order(order, log)
    rows = _read_jsonl(log)
    assert rows[0]["event_type"] == "live_order"
    assert rows[0]["mode"] == "live"
    assert rows[0]["order_id"] == "o-1"
    assert rows[0]["decision_id"] == "d-1"


def test_live_fill_log_schema(tmp_path):
    ts, iso = _now()
    log = tmp_path / "fills.jsonl"
    fill = LiveFill(
        order_id="o-1", ts=ts, timestamp=iso, side="LONG",
        fill_price=23010.5, qty=1.0, slippage=0.5, status="filled",
    )
    write_live_fill(fill, log)
    rows = _read_jsonl(log)
    assert rows[0]["event_type"] == "live_fill"
    assert rows[0]["mode"] == "live"
    assert rows[0]["status"] == "filled"


def test_live_rejection_log_schema(tmp_path):
    ts, iso = _now()
    log = tmp_path / "rejections.jsonl"
    rej = LiveRejection(
        ts=ts, timestamp=iso, decision_id="d-1", order_id=None,
        symbol="XAUUSD", side="LONG", reason="test",
        rejection_layer="micro_live_gate",
    )
    write_live_rejection(rej, log)
    rows = _read_jsonl(log)
    assert rows[0]["event_type"] == "live_rejection"
    assert rows[0]["mode"] == "live"
    assert rows[0]["rejection_layer"] == "micro_live_gate"


def test_order_fill_can_be_joined_by_order_id(tmp_path):
    ts, iso = _now()
    olog = tmp_path / "orders.jsonl"
    flog = tmp_path / "fills.jsonl"
    order = LiveOrder(
        order_id="join-1", ts=ts, timestamp=iso, decision_id="d-x",
        symbol="XAUUSD", side="LONG", qty=1.0, entry=23010.0,
        stop=22995.0, target=23040.0, confidence=0.85, reason="t",
    )
    fill = LiveFill(
        order_id="join-1", ts=ts, timestamp=iso, side="LONG",
        fill_price=23010.0, qty=1.0, slippage=0.0, status="filled",
    )
    write_live_order(order, olog)
    write_live_fill(fill, flog)
    o = _read_jsonl(olog)[0]
    f = _read_jsonl(flog)[0]
    assert o["order_id"] == f["order_id"]


def test_order_to_ai_decision_join_via_decision_id(tmp_path):
    ts, iso = _now()
    olog = tmp_path / "orders.jsonl"
    order = LiveOrder(
        order_id="o-1", ts=ts, timestamp=iso, decision_id="ai-abc",
        symbol="XAUUSD", side="LONG", qty=1.0, entry=23010.0,
        stop=22995.0, target=23040.0, confidence=0.85, reason="t",
    )
    write_live_order(order, olog)
    o = _read_jsonl(olog)[0]
    assert o["decision_id"] == "ai-abc"


def test_default_log_paths_respect_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_ORDERS_LOG", str(tmp_path / "o.jsonl"))
    monkeypatch.setenv("LIVE_FILLS_LOG", str(tmp_path / "f.jsonl"))
    monkeypatch.setenv("LIVE_REJECTIONS_LOG", str(tmp_path / "r.jsonl"))
    assert default_order_log_path() == Path(str(tmp_path / "o.jsonl"))
    assert default_fill_log_path() == Path(str(tmp_path / "f.jsonl"))
    assert default_rejection_log_path() == Path(str(tmp_path / "r.jsonl"))


def test_appends_never_overwrite(tmp_path):
    ts, iso = _now()
    log = tmp_path / "orders.jsonl"
    for i in range(3):
        order = LiveOrder(
            order_id=f"o-{i}", ts=ts, timestamp=iso, decision_id=f"d-{i}",
            symbol="XAUUSD", side="LONG", qty=1.0, entry=23010.0,
            stop=22995.0, target=23040.0, confidence=0.85, reason="t",
        )
        write_live_order(order, log)
    rows = _read_jsonl(log)
    assert len(rows) == 3
    assert [r["order_id"] for r in rows] == ["o-0", "o-1", "o-2"]
