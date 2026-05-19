"""Unit tests for PaperExecutor (Phase 6.A-3 minimal v1)."""
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

from paper.models import PaperFill, PaperPosition  # noqa: E402
from paper.paper_executor import LiveTradingForbidden, PaperExecutor  # noqa: E402
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


def _make(tmp_path: Path) -> PaperExecutor:
    return PaperExecutor(log_path=str(tmp_path / "paper_trades.jsonl"))


def _sig(side: str = "LONG", *, entry: float = 100.0,
         stop: float = 99.0, target: float = 110.0) -> Signal:
    return Signal(side, entry, stop, target, 0.8, "t")


# ---------- construction ----------

def test_refuses_live_trading_env(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(LiveTradingForbidden):
        PaperExecutor(log_path=str(tmp_path / "p.jsonl"))


def test_default_log_path_is_logs_paper_trades_jsonl(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    p = PaperExecutor()
    assert p.log_path == Path("logs/paper_trades.jsonl")


def test_env_override_log_path(monkeypatch, tmp_path):
    custom = tmp_path / "custom_paper.jsonl"
    monkeypatch.setenv("PAPER_TRADES_LOG", str(custom))
    p = PaperExecutor()
    assert p.log_path == Path(str(custom))


# ---------- submit() ----------

def test_submit_long_opens_position_and_writes_open_row(tmp_path):
    p = _make(tmp_path)
    fill = p.submit(_sig("LONG"))
    assert isinstance(fill, PaperFill)
    assert fill.side == "LONG"
    assert fill.entry == 100.0
    assert fill.mode == "paper"
    assert fill.position_id
    rows = _read_jsonl(p.log_path)
    assert len(rows) == 1
    assert rows[0]["event_type"] == "paper_open"
    assert rows[0]["side"] == "LONG"
    assert rows[0]["mode"] == "paper"
    assert rows[0]["position_id"] == fill.position_id


def test_submit_short_opens_position(tmp_path):
    p = _make(tmp_path)
    fill = p.submit(_sig("SHORT", entry=100, stop=101, target=90))
    assert fill.side == "SHORT"
    rows = _read_jsonl(p.log_path)
    assert rows[0]["side"] == "SHORT"
    assert rows[0]["mode"] == "paper"


def test_submit_flat_raises_value_error(tmp_path):
    p = _make(tmp_path)
    with pytest.raises(ValueError):
        p.submit(_sig("FLAT"))
    assert _read_jsonl(p.log_path) == []


def test_submit_unknown_side_raises_value_error(tmp_path):
    p = _make(tmp_path)
    bad = Signal("WEIRD", 100.0, 99.0, 110.0, 0.8, "t")
    with pytest.raises(ValueError):
        p.submit(bad)


def test_submit_writes_paper_mode_never_mock(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG"))
    p.submit(_sig("SHORT", entry=100, stop=101, target=90))
    rows = _read_jsonl(p.log_path)
    assert all(row["mode"] == "paper" for row in rows)
    assert not any(row.get("mode") == "mock" for row in rows)


def test_multiple_submits_have_unique_position_ids(tmp_path):
    p = _make(tmp_path)
    f1 = p.submit(_sig("LONG"))
    f2 = p.submit(_sig("LONG"))
    assert f1.position_id != f2.position_id


def test_open_positions_property_reflects_state(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG"))
    p.submit(_sig("SHORT", entry=100, stop=101, target=90))
    assert len(p.open_positions) == 2
    assert len(p.closed_positions) == 0


# ---------- tick() LONG ----------

def test_tick_long_stop_hit_closes_stopped(tmp_path):
    p = _make(tmp_path)
    fill = p.submit(_sig("LONG", entry=100, stop=99, target=110))
    closed = p.tick({"last": 98.0})
    assert len(closed) == 1
    assert closed[0].position_id == fill.position_id
    assert closed[0].close_reason == "stopped"
    assert closed[0].exit_price == 99.0
    assert closed[0].realized_pnl == pytest.approx(-1.0)
    rows = _read_jsonl(p.log_path)
    assert rows[-1]["event_type"] == "paper_close"
    assert rows[-1]["close_reason"] == "stopped"
    assert rows[-1]["mode"] == "paper"
    assert rows[-1]["position_id"] == fill.position_id


def test_tick_long_target_hit_closes_targeted(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110))
    closed = p.tick({"last": 111.0})
    assert closed[0].close_reason == "targeted"
    assert closed[0].exit_price == 110.0
    assert closed[0].realized_pnl == pytest.approx(10.0)


def test_tick_long_at_stop_exactly_closes_stopped(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110))
    closed = p.tick({"last": 99.0})
    assert closed[0].close_reason == "stopped"


def test_tick_long_at_target_exactly_closes_targeted(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110))
    closed = p.tick({"last": 110.0})
    assert closed[0].close_reason == "targeted"


# ---------- tick() SHORT ----------

def test_tick_short_stop_hit_closes_stopped(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("SHORT", entry=100, stop=101, target=90))
    closed = p.tick({"last": 102.0})
    assert closed[0].close_reason == "stopped"
    assert closed[0].exit_price == 101.0
    assert closed[0].realized_pnl == pytest.approx(-1.0)


def test_tick_short_target_hit_closes_targeted(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("SHORT", entry=100, stop=101, target=90))
    closed = p.tick({"last": 89.0})
    assert closed[0].close_reason == "targeted"
    assert closed[0].exit_price == 90.0
    assert closed[0].realized_pnl == pytest.approx(10.0)


# ---------- tick() no-op / boundary ----------

def test_tick_within_range_no_close(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110))
    closed = p.tick({"last": 100.5})
    assert closed == []
    assert len(p.open_positions) == 1


def test_tick_does_not_reclose_already_closed_position(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110))
    p.tick({"last": 98.0})
    closed_second = p.tick({"last": 95.0})
    assert closed_second == []
    rows = _read_jsonl(p.log_path)
    close_rows = [r for r in rows if r.get("event_type") == "paper_close"]
    assert len(close_rows) == 1


def test_tick_quote_without_last_is_no_op(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG"))
    closed = p.tick({"bid": 99.5, "ask": 100.5})
    assert closed == []
    assert len(p.open_positions) == 1


def test_tick_with_no_open_positions_is_no_op(tmp_path):
    p = _make(tmp_path)
    closed = p.tick({"last": 100.0})
    assert closed == []


def test_tick_multiple_positions_each_evaluated_independently(tmp_path):
    # Pick LONG and SHORT windows so that one tick triggers only one
    # position. quote 191 is above LONG.target (190) but still inside
    # SHORT's [183, 210] window. quote 182 then closes SHORT below
    # its target 183.
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=180, stop=170, target=190))
    p.submit(_sig("SHORT", entry=200, stop=210, target=183))
    p.tick({"last": 191.0})
    assert len(p.closed_positions) == 1
    assert p.closed_positions[0].side == "LONG"
    p.tick({"last": 182.0})
    assert len(p.closed_positions) == 2
    sides = sorted(c.side for c in p.closed_positions)
    assert sides == ["LONG", "SHORT"]


# ---------- log content ----------

def test_paper_close_row_position_id_matches_paper_open_row(tmp_path):
    p = _make(tmp_path)
    fill = p.submit(_sig("LONG"))
    p.tick({"last": 98.0})
    rows = _read_jsonl(p.log_path)
    open_row = next(r for r in rows if r["event_type"] == "paper_open")
    close_row = next(r for r in rows if r["event_type"] == "paper_close")
    assert open_row["position_id"] == close_row["position_id"] == fill.position_id


def test_paper_executor_never_writes_to_trades_jsonl(tmp_path):
    """L1 of the trades.jsonl isolation guarantee: paper layer writes
    only to its own configured log."""
    p = _make(tmp_path)
    p.submit(_sig("LONG"))
    p.tick({"last": 111.0})
    assert not (tmp_path / "trades.jsonl").exists()


def test_paper_close_realized_pnl_is_signed_correctly(tmp_path):
    """LONG: pnl = (exit - entry) * qty. SHORT: pnl = (entry - exit) * qty.

    Use non-overlapping windows so each position only closes when its
    own target is hit by a dedicated tick."""
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110))
    p.tick({"last": 110.0})  # LONG targeted +10
    assert p.closed_positions[0].realized_pnl == pytest.approx(10.0)

    p.submit(_sig("SHORT", entry=200, stop=210, target=190))
    p.tick({"last": 190.0})  # SHORT targeted +10
    pnls_short = [
        c.realized_pnl for c in p.closed_positions if c.side == "SHORT"
    ]
    assert pnls_short == [pytest.approx(10.0)]


def test_qty_scales_realized_pnl(tmp_path):
    p = _make(tmp_path)
    p.submit(_sig("LONG", entry=100, stop=99, target=110), qty=2.5)
    p.tick({"last": 111.0})
    assert p.closed_positions[0].realized_pnl == pytest.approx(25.0)
    assert p.closed_positions[0].qty == 2.5
