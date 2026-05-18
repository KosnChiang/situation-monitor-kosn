"""Phase-5.E scoped repo guards.

Mirrors tests/test_no_live_trading_phase5{b,c,d}.py for the operator
layer: 4 PowerShell scripts + 1 runbook + 2 test files. Re-asserts
the mock-only rules with an explicit file enumeration so a refactor
that drops a Phase-5.E file out of the wider scan would still fail
loudly here.

Rules guarded:

  1. All Phase-5.E files exist.
  2. No LIVE_TRADING set to a truthy value, no Hermes bypass flag.
  3. No broker SDK identifier in any script or in the runbook.
  4. The runbook does NOT describe a live-trading code path or
     recommend disabling safety (no example with --yolo,
     HERMES_ACCEPT_HOOKS=1, --no-verify, etc.).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE5E_SCRIPTS = [
    ROOT / "scripts" / "watch_loop_smoke.ps1",
    ROOT / "scripts" / "watch_loop_run.ps1",
    ROOT / "scripts" / "quote_feed_run.ps1",
    ROOT / "scripts" / "stop_watch_loop.ps1",
]
PHASE5E_DOCS_AND_TESTS = [
    ROOT / "docs"  / "phase5e_watch_loop_runbook.md",
    ROOT / "tests" / "test_phase5e_operator_scripts.py",
    ROOT / "tests" / "test_no_live_trading_phase5e.py",
]
PHASE5E_ALL_FILES = PHASE5E_SCRIPTS + PHASE5E_DOCS_AND_TESTS

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


@pytest.mark.parametrize("path", PHASE5E_ALL_FILES)
def test_phase5e_file_exists(path):
    assert path.exists(), f"Phase-5.E file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE5E_SCRIPTS)
def test_no_execution_bypass_in_phase5e_scripts(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


# Test files legitimately enumerate broker SDK names as denylist data; only
# the operator-facing artifacts (scripts + runbook) must be clean.
PHASE5E_OPERATOR_ARTIFACTS = PHASE5E_SCRIPTS + [ROOT / "docs" / "phase5e_watch_loop_runbook.md"]


@pytest.mark.parametrize("path", PHASE5E_OPERATOR_ARTIFACTS)
def test_no_broker_sdk_token(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for sdk in BROKER_SDKS:
            if sdk in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK token in Phase-5.E file:\n" + "\n".join(hits)


def test_runbook_does_not_describe_live_trading_path():
    """Even inside a code block, the runbook must not contain a
    LIVE_TRADING=true example. Showing 'LIVE_TRADING=true' as a
    forbidden anti-example is OK (denial markers exempt the line);
    showing it as something to run is not."""
    runbook = ROOT / "docs" / "phase5e_watch_loop_runbook.md"
    text = runbook.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"runbook:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Runbook describes a live-trading path:\n" + "\n".join(hits)


def test_runbook_does_not_recommend_disabling_safety():
    """The runbook must never suggest --no-verify, --yolo, or other
    safety bypasses. Mentioning them as forbidden (with denial markers)
    is OK; recommending them is not."""
    runbook = ROOT / "docs" / "phase5e_watch_loop_runbook.md"
    text = runbook.read_text(encoding="utf-8")
    hits = []
    bypass_patterns = [
        (re.compile(r"--yolo\b"),         "--yolo"),
        (re.compile(r"--accept-hooks\b"), "--accept-hooks"),
        (re.compile(r"--no-verify\b"),    "--no-verify"),
        (re.compile(r"--no-gpg-sign\b"),  "--no-gpg-sign"),
    ]
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in bypass_patterns:
            if pattern.search(line):
                hits.append(f"runbook:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Runbook recommends disabling safety:\n" + "\n".join(hits)


def test_runbook_contains_mock_only_assertion():
    """Sanity: the runbook should clearly assert mock-only-ness."""
    text = (ROOT / "docs" / "phase5e_watch_loop_runbook.md").read_text(encoding="utf-8")
    assert "mock-only" in text.lower(), "runbook must explicitly claim mock-only"
    assert "trades.jsonl" in text and "mock" in text, \
        "runbook must describe the trades.jsonl mode=mock audit"


def test_runtime_scripts_all_have_consistent_envelope_block():
    """A future refactor that touches one script must touch the others
    too. Cheap shape check: each runtime script must have the same
    three-line envelope-pinning block in the same order."""
    for script in (ROOT / "scripts" / "watch_loop_smoke.ps1",
                   ROOT / "scripts" / "watch_loop_run.ps1",
                   ROOT / "scripts" / "quote_feed_run.ps1"):
        text = script.read_text(encoding="utf-8")
        m = re.search(
            r'\$env:LIVE_TRADING\s*=\s*"false"[^\n]*\n'
            r'\s*\$env:EXECUTION_MODE\s*=\s*"mock"[^\n]*\n'
            r'\s*\$env:BROKER_MODE\s*=\s*"mock"',
            text,
        )
        assert m, f"{script.name}: missing canonical 3-line envelope pin block"
