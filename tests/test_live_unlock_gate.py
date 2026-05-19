"""Unit tests for live.live_unlock_gate.LiveUnlockGate (Phase 6.B-1)."""
from __future__ import annotations

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

from live.live_unlock_gate import (  # noqa: E402
    LiveUnlockForbidden,
    LiveUnlockGate,
    LiveUnlockToken,
)


def _set_all_unlock_env(monkeypatch, *, fake=True):
    """Set every required env to a valid value. Tests that want to
    exercise a specific failure clear one env after this."""
    today = f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("LIVE_READY_FLAG", today)
    monkeypatch.setenv("LIVE_TOKEN_HMAC", "abc123def456")
    monkeypatch.setenv("ALLOWED_SYMBOLS", "XAUUSD,EURUSD")
    monkeypatch.setenv("MAX_DAILY_LOSS", "50")
    monkeypatch.setenv("MAX_POSITION_SIZE", "1")
    if fake:
        monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")
        monkeypatch.delenv("LIVE_BROKER_ADAPTER_PATH", raising=False)
    else:
        monkeypatch.setenv("LIVE_BROKER_ADAPTER_PATH", "C:/Trading/live-adapters/foo.py")
        monkeypatch.delenv("FAKE_LIVE_ADAPTER", raising=False)


def _gate(tmp_path: Path) -> LiveUnlockGate:
    return LiveUnlockGate(kill_file_path=str(tmp_path / ".killswitch"))


def test_unlock_succeeds_with_all_conditions(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    g = _gate(tmp_path)
    token = g.unlock()
    assert isinstance(token, LiveUnlockToken)
    assert token.hmac_fingerprint.startswith("***")
    assert token.hmac_fingerprint.endswith("f456")
    assert "abc123def456" not in token.hmac_fingerprint
    assert token.valid_until_ts > token.granted_ts


def test_refuses_when_live_trading_not_true(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING", "false")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "LIVE_TRADING_not_true" in str(e.value)


def test_refuses_when_execution_mode_not_live(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "EXECUTION_MODE_not_live" in str(e.value)


def test_refuses_when_ready_flag_missing(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.delenv("LIVE_READY_FLAG", raising=False)
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "LIVE_READY_FLAG_mismatch" in str(e.value)


def test_refuses_when_ready_flag_wrong_date(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("LIVE_READY_FLAG", "approved-19991231")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "LIVE_READY_FLAG_mismatch" in str(e.value)


def test_refuses_when_token_hmac_empty(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("LIVE_TOKEN_HMAC", "")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "LIVE_TOKEN_HMAC_empty" in str(e.value)


def test_refuses_when_allowed_symbols_empty(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("ALLOWED_SYMBOLS", "")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "ALLOWED_SYMBOLS_empty" in str(e.value)


def test_refuses_when_max_daily_loss_empty(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("MAX_DAILY_LOSS", "")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "MAX_DAILY_LOSS_empty" in str(e.value)


def test_refuses_when_max_position_size_empty(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("MAX_POSITION_SIZE", "")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "MAX_POSITION_SIZE_empty" in str(e.value)


def test_refuses_when_no_adapter_path_and_no_fake_flag(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    monkeypatch.delenv("FAKE_LIVE_ADAPTER", raising=False)
    monkeypatch.delenv("LIVE_BROKER_ADAPTER_PATH", raising=False)
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "no_adapter_specified" in str(e.value)


def test_unlock_accepts_path_based_adapter_decl(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch, fake=False)
    token = _gate(tmp_path).unlock()
    assert isinstance(token, LiveUnlockToken)


def test_refuses_when_killswitch_file_present(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    (tmp_path / ".killswitch").write_text("trip", encoding="utf-8")
    with pytest.raises(LiveUnlockForbidden) as e:
        _gate(tmp_path).unlock()
    assert "killswitch_file_present" in str(e.value)


def test_token_expires(monkeypatch, tmp_path):
    _set_all_unlock_env(monkeypatch)
    g = LiveUnlockGate(
        kill_file_path=str(tmp_path / ".killswitch"),
        token_lifetime_seconds=0.05,
    )
    token = g.unlock()
    assert token.is_expired() is False
    time.sleep(0.1)
    assert token.is_expired() is True


def test_token_does_not_leak_full_hmac(monkeypatch, tmp_path):
    secret = "MY-SECRET-HMAC-VERY-LONG-STRING-DO-NOT-LEAK"
    _set_all_unlock_env(monkeypatch)
    monkeypatch.setenv("LIVE_TOKEN_HMAC", secret)
    token = _gate(tmp_path).unlock()
    assert secret not in token.hmac_fingerprint
    assert token.hmac_fingerprint == "***" + secret[-4:]
