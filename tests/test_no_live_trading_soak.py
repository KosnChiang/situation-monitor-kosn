"""Phase-5 soak scoped repo guards.

Mirrors tests/test_no_live_trading_phase5{b,c,d,e,f}.py for the soak
layer: 1 PowerShell orchestrator + 1 Python analyzer + 1 runbook +
3 test files + 1 fixture jsonl. Re-asserts the mock-only rules with
an explicit file enumeration so a refactor that drops a soak file
out of the wider scan would still fail loudly here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SOAK_SCRIPT  = ROOT / "scripts" / "watch_loop_soak.ps1"
SOAK_TOOL    = ROOT / "tools"   / "analyze_soak.py"
SOAK_RUNBOOK = ROOT / "docs"    / "phase5_soak_runbook.md"
SOAK_FIXTURE = ROOT / "tests"   / "fixtures" / "soak_watch_loop_clean.jsonl"

SOAK_TEST_FILES = [
    ROOT / "tests" / "test_analyze_soak.py",
    ROOT / "tests" / "test_phase5_soak_scripts.py",
    ROOT / "tests" / "test_no_live_trading_soak.py",
]

SOAK_ALL_FILES = [SOAK_SCRIPT, SOAK_TOOL, SOAK_RUNBOOK, SOAK_FIXTURE] + SOAK_TEST_FILES
SOAK_OPERATOR_ARTIFACTS = [SOAK_SCRIPT, SOAK_TOOL, SOAK_RUNBOOK, SOAK_FIXTURE]

FORBIDDEN_EXECUTION_BYPASS = [
    (re.compile(r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
     "LIVE_TRADING set to truthy"),
    (re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
     "HERMES_ACCEPT_HOOKS set to truthy"),
    (re.compile(r"--yolo\b"),         "Hermes --yolo flag"),
    (re.compile(r"--accept-hooks\b"), "Hermes --accept-hooks flag"),
]

BROKER_SDKS = [
    "ib_insync", "ibapi", "MetaTrader5",
    "ccxt.", "binance.", "alpaca_trade_api", "oandapyV20",
]

NETWORK_LIBS = ["requests", "httpx", "aiohttp", "urllib3", "urllib.request"]

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment_or_denial(line: str) -> bool:
    s = line.lstrip()
    if s.startswith("#") or s.startswith("//"):
        return True
    return bool(DENIAL_RE.search(line))


@pytest.mark.parametrize("path", SOAK_ALL_FILES)
def test_soak_file_exists(path):
    assert path.exists(), f"Soak file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", [SOAK_SCRIPT, SOAK_TOOL])
def test_no_execution_bypass(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", SOAK_OPERATOR_ARTIFACTS)
def test_no_broker_sdk_token(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for sdk in BROKER_SDKS:
            if sdk in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK token in soak operator file:\n" + "\n".join(hits)


def test_analyzer_no_network_library():
    text = SOAK_TOOL.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for lib in NETWORK_LIBS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", line):
                hits.append(f"tools/analyze_soak.py:{lineno}: '{lib}'")
    assert not hits, "Network library in analyzer:\n" + "\n".join(hits)


def test_analyzer_does_not_touch_executor_risk_strategy():
    text = SOAK_TOOL.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pkg in ("executor", "risk", "strategy"):
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(pkg)}(\.|\s)", line):
                hits.append(f"tools/analyze_soak.py:{lineno}: '{pkg}'")
    assert not hits, "Trade-origin import in analyzer:\n" + "\n".join(hits)


def test_runbook_does_not_describe_live_trading_path():
    text = SOAK_RUNBOOK.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"runbook:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Runbook describes a live-trading path:\n" + "\n".join(hits)


def test_runbook_does_not_recommend_disabling_safety():
    text = SOAK_RUNBOOK.read_text(encoding="utf-8")
    hits = []
    for pat, desc in [
        (re.compile(r"--yolo\b"),        "--yolo"),
        (re.compile(r"--accept-hooks\b"), "--accept-hooks"),
        (re.compile(r"--no-verify\b"),   "--no-verify"),
        (re.compile(r"--no-gpg-sign\b"), "--no-gpg-sign"),
    ]:
        for lineno, line in enumerate(text.splitlines(), 1):
            if _is_pure_comment_or_denial(line):
                continue
            if pat.search(line):
                hits.append(f"runbook:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Runbook recommends disabling safety:\n" + "\n".join(hits)


def test_runbook_documents_isolated_paths():
    """The runbook must mention the *_soak filenames so an operator
    can verify by reading the docs that the soak does not pollute the
    production logs."""
    text = SOAK_RUNBOOK.read_text(encoding="utf-8")
    for name in ("quotes_soak.jsonl", "watch_loop_soak.jsonl",
                 "trades_soak.jsonl", "soak_snapshots.jsonl"):
        assert name in text, f"runbook missing mention of {name}"


def test_runbook_documents_three_layer_mock_only_check_or_equivalent():
    """Operator-facing docs must surface the trades.jsonl mode=mock
    audit (the load-bearing check)."""
    text = SOAK_RUNBOOK.read_text(encoding="utf-8")
    assert "mode" in text and "mock" in text and "trades" in text
    assert "mock-only" in text.lower()
