"""Phase 6.B-3a structural guards on live/adapter_loader.py."""
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


PHASE6B3_FILES = [
    ROOT / "live" / "adapter_loader.py",
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
    r"^\s*import\s+requests\b",
    r"^\s*from\s+requests\b",
    r"^\s*import\s+httpx\b",
    r"^\s*from\s+httpx\b",
    r"^\s*import\s+aiohttp\b",
    r"^\s*from\s+aiohttp\b",
    r"^\s*from\s+urllib\.request\b",
    r"^\s*import\s+socket\b",
    r"^\s*import\s+http\b",
]

EXECUTION_BYPASS_NON_LIVE_TRADING = [
    re.compile(r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
    re.compile(r"--yolo\b"),
    re.compile(r"--accept-hooks\b"),
]


def test_phase6b3_files_exist():
    for p in PHASE6B3_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


def test_no_broker_sdk_imports():
    offenders = []
    for path in PHASE6B3_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_IMPORT_PATTERNS:
            if re.search(pat, text):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


def test_no_broker_credential_env_reads():
    offenders = []
    for path in PHASE6B3_FILES:
        text = path.read_text(encoding="utf-8")
        for name in FORBIDDEN_ENV_NAMES:
            if re.search(rf"\b{re.escape(name)}\b", text):
                offenders.append((str(path.relative_to(ROOT)), name))
    assert not offenders


def test_no_outbound_http_or_socket_at_module_top():
    offenders = []
    for path in PHASE6B3_FILES:
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN_OUTBOUND_HTTP:
            if re.search(pat, text, re.MULTILINE):
                offenders.append((str(path.relative_to(ROOT)), pat))
    assert not offenders


def test_no_execution_bypass_switches():
    offenders = []
    for path in PHASE6B3_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            for rx in EXECUTION_BYPASS_NON_LIVE_TRADING:
                if rx.search(line):
                    offenders.append(
                        (str(path.relative_to(ROOT)), line_no, line.strip()[:80])
                    )
    assert not offenders


def test_fake_branch_does_not_read_adapter_path_env(monkeypatch, tmp_path):
    """resolve_live_adapter with FAKE_LIVE_ADAPTER=true must not touch
    LIVE_BROKER_ADAPTER_PATH. We assert this by setting the path env to
    a sentinel that would trigger a path read by spying os.getenv."""
    from live.adapter_loader import resolve_live_adapter

    monkeypatch.setenv("LIVE_ADAPTER_LOADS_LOG", str(tmp_path / "loads.jsonl"))
    monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")
    monkeypatch.setenv("LIVE_BROKER_ADAPTER_PATH", str(tmp_path / "should_not_be_read.py"))

    accessed: list[str] = []
    real_getenv = os.getenv

    def _spy(name, default=None):
        accessed.append(name)
        return real_getenv(name, default)

    monkeypatch.setattr(os, "getenv", _spy)
    adapter = resolve_live_adapter()
    # The path env name must NOT have been queried in the fake branch.
    assert "LIVE_BROKER_ADAPTER_PATH" not in accessed
    # Sanity: we did read the fake flag.
    assert "FAKE_LIVE_ADAPTER" in accessed
    assert adapter is not None


def test_allowlist_self_protection_drops_repo_root(monkeypatch):
    """A misconfigured env that sets the repo root in the allowlist
    must be silently filtered by _get_allowlist."""
    from live.adapter_loader import REPO_ROOT, _get_allowlist

    monkeypatch.setenv(
        "LIVE_ADAPTER_ALLOWLIST",
        f"{REPO_ROOT},{REPO_ROOT / 'live'},C:\\Trading\\live-adapters",
    )
    allowlist = _get_allowlist()
    for p in allowlist:
        try:
            p.relative_to(REPO_ROOT)
            assert False, f"allowlist leaked repo path: {p}"
        except ValueError:
            pass


def test_loader_module_does_not_reference_broker_class_names():
    """The loader must not name any real-broker concrete class. The
    Protocol name is allowed; concrete real adapters are out-of-tree."""
    src = (ROOT / "live" / "adapter_loader.py").read_text(encoding="utf-8")
    for forbidden in (
        "IBBrokerAdapter", "CTProBrokerAdapter", "MT5BrokerAdapter",
        "ShioajiBrokerAdapter", "BinanceBrokerAdapter",
        "AlpacaBrokerAdapter",
    ):
        assert forbidden not in src, f"loader names {forbidden}"


def test_loader_not_wired_into_pipeline_yet():
    """Phase 6.B-3a is skeleton-only. The pipeline must NOT import
    adapter_loader; that's a Phase 6.B-4 concern."""
    pipeline_src = (ROOT / "live" / "pipeline.py").read_text(encoding="utf-8")
    assert "adapter_loader" not in pipeline_src, (
        "live/pipeline.py must not import adapter_loader in Phase 6.B-3a"
    )


def test_loader_not_wired_into_cli_yet():
    """The dry-run CLI must continue to hardcode FakeLiveBrokerAdapter
    in Phase 6.B-3a; loader integration is Phase 6.B-4."""
    cli_src = (ROOT / "tools" / "dry_run_live_order.py").read_text(encoding="utf-8")
    assert "adapter_loader" not in cli_src
    assert "resolve_live_adapter" not in cli_src
