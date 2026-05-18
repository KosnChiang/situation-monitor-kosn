"""Verifies the Telegram notification step in the receiver pipeline
stays in dry-run mode by default and never networks unless the operator
explicitly sets TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID with dry-run off.

Two receivers wire Telegram:
  * tools/watch_fibo_loop.py -- via --notify-mode {off,dry,live}, which
    has been in place since Phase 5.E. We just confirm it stays
    dry-only here.
  * tools/mock_fibo_signal.py -- new in Phase 6: post-submit notify
    that obeys TELEGRAM_DRY_RUN env and the --no-telegram flag.

Mock-only: no real Telegram HTTP. The TelegramBot itself prints a
"would send" line when dry-run is on, returns {ok: True, dry_run: True}.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")
os.environ.setdefault("BROKER_MODE", "mock")


# ---------------------------------------------------------------- direct TelegramBot tests

def test_telegram_bot_dry_run_via_env_never_imports_requests(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DRY_RUN", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    from notify.telegram_bot import TelegramBot
    bot = TelegramBot()
    assert bot.dry_run is True
    assert bot.enabled is False  # dry-run disables the "live" property

    result = bot.send("test message")
    assert result["dry_run"] is True
    assert result["ok"] is True
    assert result.get("skipped") is False


def test_telegram_bot_no_credentials_returns_skipped(monkeypatch):
    for k in ("TELEGRAM_DRY_RUN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)

    from notify.telegram_bot import TelegramBot
    bot = TelegramBot()
    result = bot.send("ignored")
    assert result["skipped"] is True
    assert result["ok"] is False


def test_telegram_bot_dry_run_explicit_constructor_arg(monkeypatch):
    monkeypatch.delenv("TELEGRAM_DRY_RUN", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "cid")

    from notify.telegram_bot import TelegramBot
    bot = TelegramBot(dry_run=True)
    assert bot.dry_run is True
    result = bot.send("dry constructor")
    assert result["dry_run"] is True


# ---------------------------------------------------------------- mock_fibo_signal pipeline notify

def _fibo_lines_json() -> dict:
    return {
        "input": "synthetic", "image_shape": [480, 800], "count": 1,
        "lines": [{
            "y": 200, "x_start": 10, "x_end": 700, "length": 690,
            "color_bgr": [60, 255, 60], "confidence": 0.9,
        }],
    }


def _run_signal(*, tmp_path: Path, extra_env: dict, extra_args: list[str] | None = None):
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    fibo.write_text(json.dumps(_fibo_lines_json()), encoding="utf-8")

    args = [
        sys.executable, "-m", "tools.mock_fibo_signal",
        "--fibo-lines", str(fibo),
        "--out-signals", str(sig_log),
        "--price-y", "200",
        "--tolerance-px", "5",
        "--symbol", "MOCK",
        "--min-confidence", "0.55",
        "--submit",
        "--json",
    ]
    if extra_args:
        args.extend(extra_args)
    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
        "TRADES_LOG": str(trades_log),
    }
    env.update(extra_env)
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, env=env), sig_log, trades_log


def test_mock_fibo_signal_submit_notify_dry_run_does_not_network(tmp_path):
    r, sig_log, trades_log = _run_signal(
        tmp_path=tmp_path,
        extra_env={
            "TELEGRAM_DRY_RUN": "1",
            "TELEGRAM_BOT_TOKEN": "fake",
            "TELEGRAM_CHAT_ID":   "fake",
        },
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["signal"]["side"] == "LONG"
    assert payload["fill"]["submitted"] is True
    assert payload["notify"] is not None
    assert payload["notify"]["dry_run"] is True
    assert payload["notify"]["ok"] is True


def test_mock_fibo_signal_submit_notify_no_credentials_is_skipped(tmp_path, monkeypatch):
    for k in ("TELEGRAM_DRY_RUN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    r, _, _ = _run_signal(tmp_path=tmp_path, extra_env={})
    assert r.returncode == 0, f"stderr={r.stderr}"
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["notify"]["skipped"] is True
    assert payload["notify"]["ok"] is False


def test_mock_fibo_signal_no_telegram_flag_suppresses_call(tmp_path):
    r, _, _ = _run_signal(
        tmp_path=tmp_path,
        extra_env={"TELEGRAM_DRY_RUN": "1",
                   "TELEGRAM_BOT_TOKEN": "x", "TELEGRAM_CHAT_ID": "y"},
        extra_args=["--no-telegram"],
    )
    assert r.returncode == 0, f"stderr={r.stderr}"
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["fill"]["submitted"] is True
    # --no-telegram MUST skip the call entirely
    assert payload["notify"] is None


def test_mock_fibo_signal_flat_signal_does_not_notify(tmp_path):
    """FLAT signals should never trigger Telegram even on --submit."""
    fibo = tmp_path / "fibo_lines.json"
    sig_log = tmp_path / "signals.jsonl"
    trades_log = tmp_path / "trades.jsonl"
    fibo.write_text(json.dumps(_fibo_lines_json()), encoding="utf-8")

    env = {
        **os.environ,
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
        "TRADES_LOG": str(trades_log),
        "TELEGRAM_DRY_RUN": "1",
        "TELEGRAM_BOT_TOKEN": "x",
        "TELEGRAM_CHAT_ID": "y",
    }
    # price far from any line -> FLAT
    r = subprocess.run(
        [sys.executable, "-m", "tools.mock_fibo_signal",
         "--fibo-lines", str(fibo),
         "--out-signals", str(sig_log),
         "--price-y", "1000",
         "--tolerance-px", "5",
         "--symbol", "MOCK",
         "--submit", "--json"],
        cwd=str(ROOT), capture_output=True, text=True, env=env,
    )
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["signal"]["side"] == "FLAT"
    assert payload["fill"] is None
    assert payload["notify"] is None
