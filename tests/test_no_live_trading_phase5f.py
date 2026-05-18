"""Phase-5.F scoped repo guards.

Mirrors tests/test_no_live_trading_phase5{b,c,d,e}.py for the
Phase-5.F scheduler layer: 4 PowerShell scripts + 1 runbook + 2 test
files. Re-asserts the mock-only rules + adds task-scheduler-specific
guards (no LocalSystem principal, no password storage, no root-folder
task registration).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE5F_SCRIPTS = [
    ROOT / "scripts" / "install_watch_loop_task.ps1",
    ROOT / "scripts" / "uninstall_watch_loop_task.ps1",
    ROOT / "scripts" / "watch_loop_status.ps1",
    ROOT / "scripts" / "restart_watch_loop.ps1",
]
PHASE5F_DOCS_AND_TESTS = [
    ROOT / "docs"  / "phase5f_scheduler_runbook.md",
    ROOT / "tests" / "test_phase5f_scheduler_scripts.py",
    ROOT / "tests" / "test_no_live_trading_phase5f.py",
]
PHASE5F_ALL_FILES = PHASE5F_SCRIPTS + PHASE5F_DOCS_AND_TESTS

# Test files legitimately enumerate broker / safety-bypass tokens as
# denylist data; only the operator-facing artifacts (scripts + runbook)
# must be clean.
PHASE5F_OPERATOR_ARTIFACTS = PHASE5F_SCRIPTS + [ROOT / "docs" / "phase5f_scheduler_runbook.md"]

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

SYSTEM_PRINCIPALS = ["LocalSystem", "NetworkService", "LocalService", "S-1-5-18"]

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


@pytest.mark.parametrize("path", PHASE5F_ALL_FILES)
def test_phase5f_file_exists(path):
    assert path.exists(), f"Phase-5.F file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE5F_SCRIPTS)
def test_no_execution_bypass_in_phase5f_scripts(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", PHASE5F_OPERATOR_ARTIFACTS)
def test_no_broker_sdk_token(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for sdk in BROKER_SDKS:
            if sdk in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK token in Phase-5.F operator file:\n" + "\n".join(hits)


def test_runbook_does_not_describe_live_trading_path():
    runbook = ROOT / "docs" / "phase5f_scheduler_runbook.md"
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
    runbook = ROOT / "docs" / "phase5f_scheduler_runbook.md"
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


# ---------------------------------------------------------------------------
# Scheduler-specific guards
# ---------------------------------------------------------------------------

# The runbook intentionally NAMES LocalSystem / NetworkService etc. in
# its layer-3 explanation table (saying tasks must NOT run as them).
# Markdown table cells don't trigger the comment / denial regex, so we
# scan only the PowerShell scripts for this principal guard.
@pytest.mark.parametrize("path", PHASE5F_SCRIPTS)
def test_no_localsystem_or_networkservice_principal(path):
    """Phase 5.F tasks must run as Interactive current user only. A
    Principal of LocalSystem / NetworkService / LocalService / S-1-5-18
    would let the task escape the user-env context and ignore the
    operator's mock envelope."""
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for principal in SYSTEM_PRINCIPALS:
            if principal in line:
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{principal}'")
    assert not hits, "System-level principal in Phase-5.F script:\n" + "\n".join(hits)


def test_install_script_does_not_register_at_root_or_microsoft_path():
    text = (ROOT / "scripts" / "install_watch_loop_task.ps1").read_text(encoding="utf-8")
    # The install script must use \AIFiboVisionTrader\ as the task folder
    # and must not register at \ (root) or any \Microsoft\ folder.
    register_lines = [line for line in text.splitlines() if "Register-ScheduledTask" in line]
    # The Register-ScheduledTask call uses -TaskPath $taskFolder where
    # $taskFolder = "\AIFiboVisionTrader\". Verify the var assignment.
    assert re.search(r'\$taskFolder\s*=\s*"\\AIFiboVisionTrader\\"', text), \
        "install script must assign $taskFolder = '\\AIFiboVisionTrader\\'"
    # Hard-deny risky paths.
    for risky in (r'-TaskPath\s+"\\\\"', r'-TaskPath\s+"\\Microsoft', r'-TaskPath\s+"\\\\Microsoft'):
        assert not re.search(risky, text), \
            f"install script must not register at risky path: /{risky}/"


def test_install_script_does_not_store_password():
    """`Register-ScheduledTask -Password` would let the task run while
    the user is logged off -- and would require persisting a credential
    in the task. Forbidden by F4a (Interactive + Limited)."""
    text = (ROOT / "scripts" / "install_watch_loop_task.ps1").read_text(encoding="utf-8")
    assert not re.search(r'(?:Register-ScheduledTask|New-ScheduledTaskPrincipal)[^\n]*-Password\b',
                         text), \
        "install script must not pass -Password to Register-ScheduledTask / New-ScheduledTaskPrincipal"


def test_install_script_uses_atlogon_not_atstartup():
    """AtStartup fires before any user logs in; offline-image capture
    works but live-capture would fail and -- more importantly -- the
    task would run in a context where the operator can't see logs.
    Phase 5.F is logged-on-only by design."""
    text = (ROOT / "scripts" / "install_watch_loop_task.ps1").read_text(encoding="utf-8")
    assert re.search(r'New-ScheduledTaskTrigger\s+-AtLogOn', text)
    assert "-AtStartup" not in text


def test_install_script_pins_envelope_in_action_command():
    """The Action's Argument string must include all three envelope
    pins. Caught here at static-check time so a future refactor of the
    install script can't accidentally drop them."""
    text = (ROOT / "scripts" / "install_watch_loop_task.ps1").read_text(encoding="utf-8")
    # Look for the literal pin variable definition.
    assert re.search(r"\$envPin\s*=\s*\"`\$env:LIVE_TRADING='false'", text), \
        "install script must define $envPin starting with LIVE_TRADING='false'"
    assert "`$env:EXECUTION_MODE='mock'" in text
    assert "`$env:BROKER_MODE='mock'" in text
    # And the variable must be used in the action argument.
    assert re.search(r'\$loopCmdInner\s*=\s*"\$envPin', text), \
        "install script must prepend $envPin to the loop action command"
    assert re.search(r'\$feedCmdInner\s*=\s*"\$envPin', text), \
        "install script must prepend $envPin to the feed action command"
