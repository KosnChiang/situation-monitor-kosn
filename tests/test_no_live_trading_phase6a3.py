"""Phase 6.A-3 mock-only invariants for the paper/ layer."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"


PHASE6A3_FILES = [
    ROOT / "paper" / "__init__.py",
    ROOT / "paper" / "models.py",
    ROOT / "paper" / "paper_executor.py",
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

FORBIDDEN_ENV_NAMES = [
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET", "SHIOAJI_TOKEN",
    "IB_USER", "IB_PASSWORD", "IB_ACCOUNT", "IB_GATEWAY_TOKEN",
    "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_KEY", "ALPACA_SECRET", "ALPACA_API_KEY",
    "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN", "CTPRO_ACCOUNT",
    "TRADINGVIEW_SESSION", "TRADINGVIEW_AUTH",
]

FORBIDDEN_OUTBOUND_HTTP = [
    r"\bimport\s+requests\b", r"\bfrom\s+requests\b",
    r"\bimport\s+httpx\b",    r"\bfrom\s+httpx\b",
    r"\bimport\s+aiohttp\b",  r"\bfrom\s+aiohttp\b",
    r"urllib\.request",
]

EXECUTION_BYPASS = [
    re.compile(r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


def test_phase6a3_files_exist():
    for p in PHASE6A3_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports_in_phase6a3_files():
    offenders = []
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders, f"broker SDK import in paper layer: {offenders}"


def test_no_live_order_function_names_in_phase6a3_files():
    offenders = []
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_FUNCTION_NAMES:
            if re.search(rf"\bdef\s+{name}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


def test_no_broker_credential_env_reads_in_phase6a3_files():
    offenders = []
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


def test_no_outbound_http_libraries_in_phase6a3_files():
    offenders = []
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


def test_no_execution_bypass_switches_in_phase6a3_files():
    offenders = []
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders


def test_paper_executor_refuses_live_trading(monkeypatch, tmp_path):
    from paper.paper_executor import LiveTradingForbidden, PaperExecutor
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(LiveTradingForbidden):
        PaperExecutor(log_path=str(tmp_path / "p.jsonl"))


def test_paper_files_do_not_reference_trades_jsonl_literal():
    """Paper layer writes only to paper_trades.jsonl, never trades.jsonl."""
    offenders = []
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<!paper_)trades\.jsonl", text):
            offenders.append((str(path.relative_to(ROOT)), m.group(0)))
    assert not offenders, f"trades.jsonl literal in paper layer: {offenders}"


def test_paper_does_not_import_mock_executor_or_broker_adapter():
    """Architectural isolation: paper layer is independent of
    executor.mock_executor and executor.broker_adapter. Wrapping
    happens in executor/broker_adapter.py, not the other way."""
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        assert "from executor.mock_executor" not in text, str(path)
        assert "import executor.mock_executor" not in text, str(path)
        assert "from executor.broker_adapter" not in text, str(path)
        assert "import executor.broker_adapter" not in text, str(path)


def test_paper_does_not_import_quote_module():
    """Quote IPC is file-based (logs/quotes.jsonl). Paper must not
    import quote.* in v1."""
    for path in PHASE6A3_FILES:
        text = path.read_text(encoding="utf-8")
        assert "from quote" not in text, str(path)
        assert re.search(r"^import\s+quote\b", text, re.MULTILINE) is None, str(path)


def test_paper_fill_default_mode_is_paper():
    from paper.models import PaperFill
    f = PaperFill(
        ts=1.0, timestamp="t", position_id="p", side="LONG",
        entry=100.0, stop=99.0, target=110.0, qty=1.0,
        confidence=0.8, reason="x",
    )
    assert f.mode == "paper"


def test_paper_executor_submit_and_tick_only_write_mode_paper(tmp_path):
    from paper.paper_executor import PaperExecutor
    from strategy.fibo_mob_v2 import Signal
    log = tmp_path / "p.jsonl"
    p = PaperExecutor(log_path=str(log))
    p.submit(Signal("LONG", 100.0, 99.0, 110.0, 0.8, "t"))
    p.tick({"last": 98.0})
    rows = [
        json.loads(line)
        for line in log.read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert all(r.get("mode") == "paper" for r in rows)
    assert not any(r.get("mode") == "mock" for r in rows)
