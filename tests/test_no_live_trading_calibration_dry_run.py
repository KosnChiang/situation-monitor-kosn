"""Calibration dry-run scoped repo guards.

Mirrors tests/test_no_live_trading_phase5*.py for the calibration
wizard: 1 PowerShell script + 1 runbook + 2 test files. Re-asserts
the mock-only rules + adds a wizard-specific guard: must never
write config/capture.yaml.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

WIZARD       = ROOT / "scripts" / "calibrate_chart_dry_run.ps1"
RUNBOOK      = ROOT / "docs"    / "live_capture_calibration_runbook.md"
TEST_FILES   = [
    ROOT / "tests" / "test_calibrate_dry_run_script.py",
    ROOT / "tests" / "test_no_live_trading_calibration_dry_run.py",
]

CALIBRATION_ALL_FILES = [WIZARD, RUNBOOK] + TEST_FILES
CALIBRATION_OPERATOR_ARTIFACTS = [WIZARD, RUNBOOK]

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


@pytest.mark.parametrize("path", CALIBRATION_ALL_FILES)
def test_calibration_file_exists(path):
    assert path.exists(), f"calibration file missing: {path.relative_to(ROOT)}"


def test_wizard_no_execution_bypass():
    text = WIZARD.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"wizard:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", CALIBRATION_OPERATOR_ARTIFACTS)
def test_calibration_no_broker_sdk_token(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for sdk in BROKER_SDKS:
            if sdk in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK token in calibration operator file:\n" + "\n".join(hits)


def test_wizard_no_npm_install():
    text = WIZARD.read_text(encoding="utf-8")
    assert not re.search(r'\bnpm\s+install\b', text)


def test_wizard_does_not_write_production_config():
    """Re-asserts the LC5a invariant from a separate test file so a
    refactor that touches tests/test_calibrate_dry_run_script.py
    can't accidentally relax it. This is the load-bearing claim
    for the calibration dry-run: the wizard NEVER mutates
    config/capture.yaml; the operator's final paste is manual."""
    code = "\n".join(
        line for line in WIZARD.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    forbidden = []
    for cfg in (r'config\\capture\.yaml', r'config/capture\.yaml'):
        forbidden += [
            rf'Set-Content[^\n]*{cfg}',
            rf'Add-Content[^\n]*{cfg}',
            rf'Out-File[^\n]*{cfg}',
            rf'>\s*[\'"]?{cfg}',
            rf'>>\s*[\'"]?{cfg}',
            rf'\[(?:System\.)?IO\.File\]::WriteAllText\([^\n]*{cfg}',
        ]
    hits = [pat for pat in forbidden if re.search(pat, code)]
    assert not hits, f"wizard must never write config/capture.yaml. Hits: {hits}"


def test_runbook_does_not_describe_live_trading_path():
    text = RUNBOOK.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"runbook:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Runbook describes a live-trading path:\n" + "\n".join(hits)


def test_runbook_does_not_recommend_disabling_safety():
    text = RUNBOOK.read_text(encoding="utf-8")
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


def test_wizard_does_not_invoke_broker_or_quote_provider_import():
    """The wizard's only python invocation should be `python -m
    tools.calibrate_chart`. It must not import or invoke any
    broker-side or quote-provider module."""
    code = "\n".join(
        line for line in WIZARD.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )
    forbidden_modules = [
        "tools.quote_feed",
        "quote.quote_provider",
        "executor.mock_executor",
        "risk.risk_gate",
        "strategy.fibo_mob_v2",
    ]
    for mod in forbidden_modules:
        assert mod not in code, f"wizard must not invoke {mod}"
