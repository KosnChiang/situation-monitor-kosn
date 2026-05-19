"""Phase 7.A structural guards.

Pins:
  * No real-broker SDK on the main repo's runtime path.
  * The Shioaji SDK lives only inside templates/.
  * requirements.txt does not include shioaji.
  * AI-swing layer does no outbound HTTP, no broker credential reads.
  * CTPro is not referenced anywhere except in tests/docs.
  * Webhook secret remains the only env name read.
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


PHASE7_FILES = [
    ROOT / "ai_swing" / "__init__.py",
    ROOT / "ai_swing" / "context.py",
    ROOT / "ai_swing" / "decision.py",
    ROOT / "ai_swing" / "engine.py",
    ROOT / "ai_swing" / "tmf_pnl.py",
    ROOT / "ai_swing" / "logging.py",
    ROOT / "app" / "webhook_ai_swing.py",
    ROOT / "tools" / "run_live_order.py",
    ROOT / "tools" / "run_ai_swing_once.py",
    ROOT / "tools" / "watch_ai_swing_webhook.py",
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

FORBIDDEN_ENV_NAMES = [
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY", "SHIOAJI_SECRET",
    "IB_USER", "IB_PASSWORD", "IB_ACCOUNT", "IB_GATEWAY_TOKEN",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_KEY", "ALPACA_SECRET", "ALPACA_API_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN", "CTPRO_ACCOUNT",
    "TRADINGVIEW_SESSION", "TRADINGVIEW_AUTH",
]

FORBIDDEN_OUTBOUND_HTTP = [
    r"^\s*import\s+requests\b", r"^\s*from\s+requests\b",
    r"^\s*import\s+httpx\b",    r"^\s*from\s+httpx\b",
    r"^\s*import\s+aiohttp\b",  r"^\s*from\s+aiohttp\b",
    r"^\s*from\s+urllib\.request\b",
]


# ---------- Phase 7 source files: no broker SDK / no credentials ----

def test_phase7_files_exist():
    for p in PHASE7_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports_in_phase7_files():
    offenders = []
    for path in PHASE7_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


def test_no_broker_credential_env_reads_in_phase7_files():
    offenders = []
    for path in PHASE7_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders, f"forbidden credential env in 7.A layer: {offenders}"


def test_no_outbound_http_at_module_top_in_phase7_files():
    offenders = []
    for path in PHASE7_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text, re.MULTILINE):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


# ---------- main repo: no shioaji in requirements.txt -----------

def test_shioaji_not_in_main_requirements_txt():
    req = ROOT / "requirements.txt"
    if not req.exists():
        return
    content = req.read_text(encoding="utf-8").lower()
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if re.match(r"^shioaji(\s*[<>=!~].*)?$", stripped):
            pytest.fail(f"shioaji listed in main repo requirements.txt: {line!r}")


# ---------- main repo source: no shioaji imports anywhere ------

def test_no_shioaji_import_in_main_repo_source():
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules",
        "__pycache__", ".pytest_cache",
        "tests", "docs",
        "templates",  # operator copies templates OUT of main repo
    }
    pat = re.compile(r"^\s*(?:import\s+shioaji|from\s+shioaji\b)", re.MULTILINE)
    offenders = []
    for p in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pat.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, (
        "shioaji import in main repo source (must be in templates/ only): "
        f"{offenders}"
    )


# ---------- AI-swing webhook reads only its own secret ------------

def test_ai_swing_webhook_only_reads_its_own_secret():
    """The AI-swing webhook layer must read AI_SWING_WEBHOOK_SECRET
    only, not any other secret env name."""
    target_files = [
        ROOT / "app" / "webhook_ai_swing.py",
        ROOT / "app" / "main.py",
    ]
    forbidden = (
        "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
        "TRADINGVIEW_SESSION", "TRADINGVIEW_AUTH",
        "TRADINGVIEW_TOKEN", "TRADINGVIEW_SESSIONID",
    )
    offenders = []
    for path in target_files:
        text = path.read_text(encoding="utf-8")
        for name in forbidden:
            if name in text:
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


# ---------- no CTPro references in production source --------------

def test_no_ctpro_env_or_identifier_in_main_repo_source():
    """CTPro env names / identifiers must not appear in main repo
    source. Brand-name mentions in docstrings (e.g. "a real CTPro / IB
    binding") are documentation of what is OUT-OF-SCOPE and are
    allowed -- we only flag actual code-level references."""
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules",
        "__pycache__", ".pytest_cache",
        "tests", "docs",
    }
    # Flag CTPRO_ as an env-name prefix or as a Python identifier prefix.
    pat = re.compile(r"\bCTPRO_[A-Z]")
    offenders = []
    for p in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pat.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, f"CTPro identifier in main repo source: {offenders}"


# ---------- AI-swing layer never imports adapter_loader (decoupled) -

def test_ai_swing_layer_does_not_invoke_adapter_loader():
    """The Phase 7.A AI-swing layer dispatches via the in-repo
    FakeLiveBrokerAdapter directly; it does NOT call resolve_live_adapter
    or load_live_adapter. External adapter loading happens via
    tools.dry_run_live_order / tools.run_live_order, which the AI-swing
    layer does not call."""
    for src_path in (
        ROOT / "app" / "webhook_ai_swing.py",
        ROOT / "ai_swing" / "engine.py",
    ):
        src = src_path.read_text(encoding="utf-8")
        assert "resolve_live_adapter" not in src, str(src_path)
        assert "load_live_adapter" not in src, str(src_path)


# ---------- AI-swing engine writes mode="paper" by default --------

def test_engine_default_mode_is_paper():
    from ai_swing.engine import decide
    from ai_swing.context import build_chart_context_from_tv_alert
    alert = {
        "symbol": "TMFR1", "timeframe": "5", "bar_time": "t",
        "open":  23015.0, "high": 23022.0, "low": 22995.0, "close": 23018.0,
        "prev_ohlc": {"open": 23000.0, "high": 23080.0, "low": 22970.0, "close": 23055.0},
        "fibo": {
            "base": 23026.25, "range": 110.0,
            "nearest_ratio": 0.5, "nearest_price": 23070.0,
            "touch_support": False, "touch_resistance": False,
            "zone": "neutral",
        },
        "rsi": {"value": 50.0, "ma": 50.0, "state": "neutral",
                "bull_div": False, "bear_div": False},
        "pivot": {"last_high": 23110.0, "last_low": 22960.0},
        "signal": {
            "buy": False, "sell": False,
            "combo_buy": False, "combo_sell": False,
            "div_buy": False, "div_sell": False, "touch_fibo": False,
        },
    }
    ctx = build_chart_context_from_tv_alert(alert, context_id="x", ts=1.0, timestamp="t")
    d = decide(ctx=ctx)
    assert d.mode == "paper"
