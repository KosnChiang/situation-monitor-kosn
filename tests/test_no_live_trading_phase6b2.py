"""Phase 6.B-2 structural guards on the pipeline + CLI."""
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


PHASE6B2_FILES = [
    ROOT / "live" / "pipeline.py",
    ROOT / "tools" / "dry_run_live_order.py",
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
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET", "SHIOAJI_TOKEN",
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

EXECUTION_BYPASS_NON_LIVE_TRADING = [
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


def test_phase6b2_files_exist():
    for p in PHASE6B2_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports():
    offenders = []
    for path in PHASE6B2_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


def test_no_broker_credential_env_reads():
    offenders = []
    for path in PHASE6B2_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


def test_no_outbound_http_at_module_top():
    offenders = []
    for path in PHASE6B2_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text, re.MULTILINE):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


def test_no_non_live_trading_execution_bypass_switches():
    offenders = []
    for path in PHASE6B2_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS_NON_LIVE_TRADING:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders


def test_pipeline_does_not_dynamically_load_adapter_path():
    """LIVE_BROKER_ADAPTER_PATH dynamic loading is a Phase 6.B-3
    concern. The pipeline must NOT read this env nor invoke
    importlib.util to load a path-based adapter."""
    src = (ROOT / "live" / "pipeline.py").read_text(encoding="utf-8")
    assert "LIVE_BROKER_ADAPTER_PATH" not in src, (
        "pipeline.py must not reference LIVE_BROKER_ADAPTER_PATH; "
        "dynamic adapter loading is Phase 6.B-3"
    )
    assert "spec_from_file_location" not in src
    assert "import_module" not in src


def test_pipeline_only_imports_fake_live_adapter_concrete_class():
    """The pipeline source imports FakeLiveBrokerAdapter exactly once
    and does NOT mention any other concrete adapter class name."""
    src = (ROOT / "live" / "pipeline.py").read_text(encoding="utf-8")
    assert "FakeLiveBrokerAdapter" in src
    # No real-broker concrete class names appear:
    for forbidden in (
        "IBBrokerAdapter", "CTProBrokerAdapter", "MT5BrokerAdapter",
        "ShioajiBrokerAdapter", "BinanceBrokerAdapter",
    ):
        assert forbidden not in src, f"pipeline references {forbidden}"


def test_cli_only_imports_fake_live_adapter():
    src = (ROOT / "tools" / "dry_run_live_order.py").read_text(encoding="utf-8")
    assert "FakeLiveBrokerAdapter" in src
    for forbidden in (
        "IBBrokerAdapter", "CTProBrokerAdapter", "MT5BrokerAdapter",
        "ShioajiBrokerAdapter", "BinanceBrokerAdapter",
    ):
        assert forbidden not in src, f"CLI references {forbidden}"


def test_pipeline_requires_adapter_argument():
    """Constructor must refuse adapter=None so a no-adapter pipeline
    cannot accidentally hit the live path."""
    from live.pipeline import LivePipeline
    with pytest.raises((TypeError, ValueError)):
        LivePipeline(adapter=None)  # type: ignore[arg-type]


def test_pipeline_layers_constant_is_complete():
    """The PIPELINE_LAYERS tuple must list every rejection layer the
    pipeline can emit; a forgotten layer in this list is a release-
    blocker that breaks audit join scripts."""
    from live.pipeline import PIPELINE_LAYERS
    expected = {
        "ai_decision_validator",
        "live_unlock_gate",
        "kill_switch",
        "not_tradable",
        "micro_live_gate",
        "adapter",
    }
    assert set(PIPELINE_LAYERS) == expected
