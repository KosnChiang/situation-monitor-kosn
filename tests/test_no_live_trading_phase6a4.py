"""Phase 6.A-4 mock-only invariants for receivers ⇄ Router wiring.

Scope:
  * tools/mock_fibo_signal.py         (modified to add --route)
  * app/main.py                       (modified to add /webhook/tradingview)
  * app/webhook_tradingview.py        (new)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"


PHASE6A4_FILES = [
    ROOT / "tools" / "mock_fibo_signal.py",
    ROOT / "app"   / "main.py",
    ROOT / "app"   / "webhook_tradingview.py",
]


FORBIDDEN_IMPORT_PATTERNS = [
    r"\bimport\s+ib_insync\b", r"\bfrom\s+ib_insync\b",
    r"\bimport\s+ibapi\b",     r"\bfrom\s+ibapi\b",
    r"\bimport\s+alpaca\b",    r"\bfrom\s+alpaca\b",
    r"\bimport\s+ccxt\b",      r"\bfrom\s+ccxt\b",
    r"\bimport\s+binance\b",   r"\bfrom\s+binance\b",
    r"\bimport\s+oandapyV20\b", r"\bfrom\s+oandapyV20\b",
    r"\bimport\s+MetaTrader5\b", r"\bfrom\s+MetaTrader5\b",
    r"\bimport\s+shioaji\b",   r"\bfrom\s+shioaji\b",
]

FORBIDDEN_FUNCTION_NAMES = [
    "place_order", "submit_order", "send_order",
    "live_order", "real_order", "place_live_order", "execute_live",
]

# Webhook layer may legitimately reference TRADINGVIEW_WEBHOOK_SECRET
# (its own env). It must NOT reference any other broker / session
# credential env name.
FORBIDDEN_ENV_NAMES = [
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET", "SHIOAJI_TOKEN",
    "IB_USER", "IB_PASSWORD", "IB_ACCOUNT", "IB_GATEWAY_TOKEN",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_KEY", "ALPACA_SECRET", "ALPACA_API_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN", "CTPRO_ACCOUNT",
    "TRADINGVIEW_SESSION", "TRADINGVIEW_AUTH",
]

# app/main.py imports notify.telegram_bot which itself imports
# requests at function scope. That dotted import does not appear at
# top of app/main.py's source, so a strict scan is fine.
FORBIDDEN_OUTBOUND_HTTP = [
    r"^\s*import\s+requests\b",
    r"^\s*from\s+requests\b",
    r"^\s*import\s+httpx\b",
    r"^\s*from\s+httpx\b",
    r"^\s*import\s+aiohttp\b",
    r"^\s*from\s+aiohttp\b",
    r"^\s*from\s+urllib\.request\b",
]

EXECUTION_BYPASS = [
    re.compile(r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


def test_phase6a4_files_exist():
    for p in PHASE6A4_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports():
    offenders = []
    for path in PHASE6A4_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"broker SDK import in 6.A-4 layer: {offenders}"


def test_no_live_order_function_names():
    offenders = []
    for path in PHASE6A4_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_FUNCTION_NAMES:
            if re.search(rf"\bdef\s+{name}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


def test_no_broker_credential_env_reads():
    offenders = []
    for path in PHASE6A4_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"forbidden credential env reference: {offenders}"


def test_no_outbound_http_libraries_at_module_top():
    """Top-of-file imports must not pull in network libraries.
    Function-scope imports (e.g. notify.telegram_bot's `import requests`
    inside its `.send()` method) are out of scope."""
    offenders = []
    for path in PHASE6A4_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text, re.MULTILINE):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"outbound HTTP import in 6.A-4 layer: {offenders}"


def test_no_execution_bypass_switches():
    offenders = []
    for path in PHASE6A4_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders, f"execution bypass switch: {offenders}"


def test_webhook_module_only_reads_its_own_secret_env():
    """The TradingView webhook layer reads exactly one TV-prefixed env
    (TRADINGVIEW_WEBHOOK_SECRET + TRADINGVIEW_WEBHOOK_LOG +
    TRADINGVIEW_REQUIRE_APPROVAL). It must NOT read any TV session /
    auth credential."""
    webhook_files = [
        ROOT / "app" / "webhook_tradingview.py",
        ROOT / "app" / "main.py",
    ]
    forbidden = ["TRADINGVIEW_SESSION", "TRADINGVIEW_AUTH"]
    offenders = []
    for path in webhook_files:
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in text:
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"webhook layer references forbidden env: {offenders}"


def test_app_main_refuses_live_trading_at_import(tmp_path, monkeypatch):
    """If LIVE_TRADING is true at app load time, RiskGate raises
    LiveTradingForbidden and the existing module-level try/except
    converts it to SystemExit. Re-importing app.main with the env
    flipped triggers the same path."""
    import importlib
    import sys as _sys

    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    monkeypatch.setenv("TRADES_LOG", str(tmp_path / "trades.jsonl"))

    for mod in ("app.main",):
        if mod in _sys.modules:
            del _sys.modules[mod]
    with pytest.raises(SystemExit):
        importlib.import_module("app.main")

    # restore mock mode so subsequent tests are unaffected
    monkeypatch.setenv("LIVE_TRADING", "false")
    for mod in ("app.main",):
        if mod in _sys.modules:
            del _sys.modules[mod]
    importlib.import_module("app.main")


def test_mock_fibo_signal_route_helper_does_not_call_mock_executor_directly():
    """The new --route path delegates to ExecutorRouter, not directly
    to MockExecutor. The old --submit helper retains its direct
    MockExecutor call; --route must not duplicate it."""
    src = (ROOT / "tools" / "mock_fibo_signal.py").read_text(encoding="utf-8")
    # crude: the _route_through_pipeline body should not contain
    # MockExecutor (the wrapping happens inside MockBrokerAdapter
    # which Router instantiates internally).
    start = src.find("def _route_through_pipeline")
    end = src.find("def _notify_telegram", start)
    assert start != -1 and end != -1
    route_body = src[start:end]
    assert "MockExecutor" not in route_body, (
        "--route helper must not instantiate MockExecutor directly; "
        "it must go through ExecutorRouter."
    )


def test_webhook_redact_token_is_present_in_source():
    """A defensive belt-and-braces: the redaction token must appear in
    the webhook layer source so a future refactor that drops the
    redaction is easy to spot in review."""
    src = (ROOT / "app" / "webhook_tradingview.py").read_text(encoding="utf-8")
    assert "REDACTED" in src
    assert '"***"' in src or "'***'" in src
