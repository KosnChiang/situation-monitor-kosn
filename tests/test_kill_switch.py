"""Unit tests for live.kill_switch.KillSwitch (Phase 6.B-1)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from live.kill_switch import KillSwitch  # noqa: E402


def _ks(tmp_path: Path, **kw) -> KillSwitch:
    return KillSwitch(
        kill_file_path=str(tmp_path / ".killswitch"),
        **kw,
    )


def test_inactive_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path)
    active, reasons = k.is_active()
    assert active is False
    assert reasons == []


def test_active_when_kill_file_present(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path)
    (tmp_path / ".killswitch").write_text("trip", encoding="utf-8")
    active, reasons = k.is_active()
    assert active is True
    assert any("kill_file_present" in r for r in reasons)


def test_active_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_KILL_SWITCH", "1")
    k = _ks(tmp_path)
    active, reasons = k.is_active()
    assert active is True
    assert any("env_kill_switch" in r for r in reasons)


def test_active_when_env_set_truthy_variants(tmp_path, monkeypatch):
    for v in ("true", "yes", "on"):
        monkeypatch.setenv("LIVE_KILL_SWITCH", v)
        k = _ks(tmp_path)
        active, _ = k.is_active()
        assert active is True, f"{v!r} should trip"


def test_inactive_when_env_set_falsy(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_KILL_SWITCH", "0")
    k = _ks(tmp_path)
    active, _ = k.is_active()
    assert active is False


def test_session_trip_activates(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path)
    k.trip("manual_test")
    active, reasons = k.is_active()
    assert active is True
    assert any("manual_test" in r for r in reasons)


def test_daily_loss_auto_trips(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path, max_daily_loss=50.0, max_consecutive_losses=10)
    k.record_loss(60.0)
    active, reasons = k.is_active()
    assert active is True
    assert any("daily_loss_exceeded" in r for r in reasons)


def test_consecutive_losses_auto_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path, max_daily_loss=1000.0, max_consecutive_losses=3)
    k.record_loss(1.0)
    k.record_loss(1.0)
    k.record_loss(1.0)
    active, reasons = k.is_active()
    assert active is True
    assert any("consecutive_losses" in r for r in reasons)


def test_win_resets_consecutive_losses_counter(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path, max_daily_loss=1000.0, max_consecutive_losses=3)
    k.record_loss(1.0)
    k.record_loss(1.0)
    assert k.consecutive_losses == 2
    k.record_win(5.0)
    assert k.consecutive_losses == 0


def test_trip_is_sticky_and_appends_reasons(tmp_path, monkeypatch):
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path)
    k.trip("reason_a")
    k.trip("reason_b")
    active, reasons = k.is_active()
    assert active is True
    assert any("reason_a" in r for r in reasons)
    assert any("reason_b" in r for r in reasons)


def test_v1_does_not_auto_close_positions(tmp_path, monkeypatch):
    """v1 promise: trip stops new orders but does NOT close existing
    positions. The KillSwitch object has no close/cancel method."""
    monkeypatch.delenv("LIVE_KILL_SWITCH", raising=False)
    k = _ks(tmp_path)
    k.trip("test")
    assert not hasattr(k, "close_positions")
    assert not hasattr(k, "flatten")
    assert not hasattr(k, "emergency_close")
