"""Phase-5.E operator-script static checks.

The four PowerShell wrappers in scripts/watch_loop_*.ps1 +
scripts/quote_feed_run.ps1 + scripts/stop_watch_loop.ps1 are pure
operator UX -- they wrap existing Python CLIs without modifying any
production logic. These tests treat each script as a text artifact
and assert the safety properties an operator depends on:

  * runtime scripts pin the mock envelope and abort if it's hot;
  * no broker SDK / credential env name appears in any script;
  * no Hermes --yolo / --accept-hooks bypass;
  * no `npm install`;
  * the smoke script never passes --submit unless the -Submit switch
    was used;
  * PID files use the .pid.log extension so they land under the
    existing logs/*.log .gitignore rule (no .gitignore modification);
  * the runbook exists, references each script, and contains the
    operator checklist + stop procedure sections.

No subprocess invocations: PowerShell availability varies across CI
environments, and the wrapped Python CLIs are already covered by the
21-test pytest suite in tests/test_watch_fibo_loop.py.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")
os.environ.setdefault("BROKER_MODE", "mock")

SCRIPTS_DIR = ROOT / "scripts"

# Runtime scripts pin + refuse the mock envelope. The stop script
# is deliberately exempt: it is a shutdown command, not a runtime.
RUNTIME_SCRIPTS = [
    SCRIPTS_DIR / "watch_loop_smoke.ps1",
    SCRIPTS_DIR / "watch_loop_run.ps1",
    SCRIPTS_DIR / "quote_feed_run.ps1",
]
STOP_SCRIPT = SCRIPTS_DIR / "stop_watch_loop.ps1"
ALL_SCRIPTS = RUNTIME_SCRIPTS + [STOP_SCRIPT]

RUNBOOK = ROOT / "docs" / "phase5e_watch_loop_runbook.md"


# ---------------------------------------------------------------------------
# Runtime scripts: pin + refuse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", RUNTIME_SCRIPTS)
def test_runtime_script_pins_mock_envelope(script):
    text = script.read_text(encoding="utf-8")
    assert re.search(r'\$env:LIVE_TRADING\s*=\s*"false"', text), \
        f"{script.name}: missing LIVE_TRADING=false pin"
    assert re.search(r'\$env:EXECUTION_MODE\s*=\s*"mock"', text), \
        f"{script.name}: missing EXECUTION_MODE=mock pin"
    assert re.search(r'\$env:BROKER_MODE\s*=\s*"mock"', text), \
        f"{script.name}: missing BROKER_MODE=mock pin"


@pytest.mark.parametrize("script", RUNTIME_SCRIPTS)
def test_runtime_script_refuses_when_envelope_hot(script):
    text = script.read_text(encoding="utf-8")
    # Must explicitly check the operator's session env for each var.
    assert re.search(r'\$env:LIVE_TRADING\s+-and\s+\$env:LIVE_TRADING\s+-ne\s+"false"', text), \
        f"{script.name}: missing LIVE_TRADING hot-envelope check"
    assert re.search(r'\$env:EXECUTION_MODE\s+-and\s+\$env:EXECUTION_MODE\s+-ne\s+"mock"', text), \
        f"{script.name}: missing EXECUTION_MODE hot-envelope check"
    assert re.search(r'\$env:BROKER_MODE\s+-and\s+\$env:BROKER_MODE\s+-ne\s+"mock"', text), \
        f"{script.name}: missing BROKER_MODE hot-envelope check"
    # Must surface a "Refusing to run" message via Write-Error.
    assert re.search(r'Write-Error\s+"Refusing to run', text), \
        f"{script.name}: hot-envelope check must Write-Error with 'Refusing to run'"


@pytest.mark.parametrize("script", RUNTIME_SCRIPTS)
def test_runtime_script_blacklists_broker_credentials(script):
    text = script.read_text(encoding="utf-8")
    # The blacklist loop iterates forbidden cred names and refuses if any is set.
    for cred in ("SHIOAJI_API_KEY", "CTPRO_USER", "IB_PASSWORD"):
        assert cred in text, f"{script.name}: blacklist missing {cred}"
    assert re.search(r'Refusing to run:\s*forbidden broker credential', text), \
        f"{script.name}: missing credential refuse message"


# ---------------------------------------------------------------------------
# All scripts: no Hermes bypass, no broker SDK, no LIVE_TRADING=true, no npm
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_no_yolo_or_accept_hooks(script):
    text = script.read_text(encoding="utf-8")
    forbidden = [r"--yolo\b", r"--accept-hooks\b",
                 r"HERMES_ACCEPT_HOOKS\s*=\s*['\"]?(?:1|true|yes|on)"]
    for pat in forbidden:
        assert not re.search(pat, text), f"{script.name}: forbidden token /{pat}/"


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_no_broker_sdk_token(script):
    text = script.read_text(encoding="utf-8")
    # Credential ENV NAMES (e.g. SHIOAJI_API_KEY) are expected in the
    # blacklist for the runtime scripts; the SDK module names below are
    # different and must not appear at all.
    sdks = ["ib_insync", "ibapi", "ccxt.", "oandapyV20"]
    for sdk in sdks:
        assert sdk not in text, f"{script.name}: forbidden broker SDK token {sdk!r}"


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_no_live_trading_true_anywhere(script):
    text = script.read_text(encoding="utf-8")
    assert not re.search(
        r"LIVE_TRADING\s*=\s*['\"]?(?:1|true|yes|on)\b",
        text,
        re.IGNORECASE,
    ), f"{script.name}: must not set LIVE_TRADING truthy"


@pytest.mark.parametrize("script", ALL_SCRIPTS)
def test_no_npm_install(script):
    text = script.read_text(encoding="utf-8")
    assert not re.search(r"\bnpm\s+install\b", text), \
        f"{script.name}: must not run npm install"


# ---------------------------------------------------------------------------
# Smoke script: --submit is opt-in
# ---------------------------------------------------------------------------

def test_smoke_script_does_not_include_submit_by_default():
    text = (SCRIPTS_DIR / "watch_loop_smoke.ps1").read_text(encoding="utf-8")
    # --submit must appear EXACTLY once, and only inside the `if ($Submit)` arm.
    matches = list(re.finditer(r'--submit\b', text))
    assert len(matches) == 1, \
        f"watch_loop_smoke.ps1: expected exactly one '--submit' occurrence, found {len(matches)}"
    pre = text[:matches[0].start()]
    # The most recent `if (...)` clause before `--submit` must mention $Submit.
    last_if = list(re.finditer(r'if\s*\(([^)]*)\)', pre))
    assert last_if, "watch_loop_smoke.ps1: --submit not inside any if(...) block"
    last_cond = last_if[-1].group(1)
    assert "$Submit" in last_cond, \
        f"watch_loop_smoke.ps1: --submit must be guarded by if (\\$Submit), found if({last_cond})"


def test_smoke_script_isolates_trades_log():
    text = (SCRIPTS_DIR / "watch_loop_smoke.ps1").read_text(encoding="utf-8")
    assert re.search(r'\$env:TRADES_LOG\s*=\s*\$tradesLog', text) or \
           re.search(r'\$env:TRADES_LOG\s*=\s*["\']?logs[\\/]trades_smoke', text), \
        "smoke script must redirect TRADES_LOG into a smoke-only path"


# ---------------------------------------------------------------------------
# PID file extension trick (gitignored via logs/*.log)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script,expected_pid", [
    (SCRIPTS_DIR / "watch_loop_run.ps1", "watch_loop.pid.log"),
    (SCRIPTS_DIR / "quote_feed_run.ps1", "quote_feed.pid.log"),
])
def test_runtime_script_writes_pid_log(script, expected_pid):
    text = script.read_text(encoding="utf-8")
    # The PID file path must be assigned to a $pidFile variable and the
    # variable must then be Out-File'd with $PID. We don't require the
    # literal filename on the same line as Out-File (the scripts assign
    # via $pidFile = "...pid.log" first, then pipe $PID | Out-File $pidFile).
    assert re.search(rf'\$pidFile\s*=\s*"[^"]*{re.escape(expected_pid)}"', text), \
        f"{script.name}: expected '$pidFile = \"...{expected_pid}\"' assignment"
    assert re.search(r'\$PID\s*\|\s*Out-File\s+-FilePath\s+\$pidFile', text), \
        f"{script.name}: PID file must be written via '$PID | Out-File -FilePath $pidFile'"


def test_pid_log_extension_is_covered_by_existing_gitignore():
    """The whole point of the .pid.log extension trick is that
    `logs/*.log` already exists in .gitignore. Verify so future
    refactors don't silently break the coverage."""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "logs/*.log" in gi, \
        ".gitignore must keep the 'logs/*.log' rule that covers our PID files"


def test_stop_script_reads_pid_log_files():
    text = (SCRIPTS_DIR / "stop_watch_loop.ps1").read_text(encoding="utf-8")
    assert "watch_loop.pid.log" in text, "stop script must reference watch_loop.pid.log"
    assert "quote_feed.pid.log" in text, "stop script must reference quote_feed.pid.log"
    assert "Stop-Process" in text, "stop script must call Stop-Process"


# ---------------------------------------------------------------------------
# Runbook structure
# ---------------------------------------------------------------------------

def test_runbook_exists():
    assert RUNBOOK.exists(), f"runbook missing: {RUNBOOK}"


def test_runbook_references_each_script():
    text = RUNBOOK.read_text(encoding="utf-8")
    for script in ALL_SCRIPTS:
        assert script.name in text, f"runbook does not mention {script.name}"


def test_runbook_has_operator_checklist_section():
    text = RUNBOOK.read_text(encoding="utf-8")
    # Section 8 is the operator checklist per the agreed structure (D5a).
    assert re.search(r'^##\s*8\.\s', text, re.MULTILINE), \
        "runbook missing section ## 8"
    assert "Operator Checklist" in text, "runbook missing 'Operator Checklist' heading text"


def test_runbook_has_stop_procedure_section():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert re.search(r'^##\s*5\.\s', text, re.MULTILINE), \
        "runbook missing section ## 5 (停止 loop)"
    assert "stop_watch_loop.ps1" in text, "runbook section 5 must reference stop_watch_loop.ps1"


def test_runbook_documents_three_layer_mock_only_check():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "3 層獨立驗證" in text or "3 layer" in text.lower(), \
        "runbook missing the 3-layer mock-only check"
    assert "RiskGate" in text, "runbook must mention RiskGate for the python-boundary layer"
    assert "trades.jsonl" in text, "runbook must mention trades.jsonl for the audit layer"
