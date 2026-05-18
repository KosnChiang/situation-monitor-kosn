"""Phase-5.F scheduler-script static checks.

The four PowerShell scripts under scripts/ that install / uninstall /
restart / status the Phase-5.D watch loop and Phase-5.5 quote feed
as Windows Scheduled Tasks. Pure operator UX; zero production logic.

These tests treat each script as a text artifact. The actual
Register-ScheduledTask / Stop-ScheduledTask / Unregister-ScheduledTask
calls are NOT invoked from pytest -- doing so would pollute the host
machine's Task Scheduler. Manual end-to-end verification is in
docs/phase5f_scheduler_runbook.md sections 1-6.
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

INSTALL    = SCRIPTS_DIR / "install_watch_loop_task.ps1"
UNINSTALL  = SCRIPTS_DIR / "uninstall_watch_loop_task.ps1"
STATUS     = SCRIPTS_DIR / "watch_loop_status.ps1"
RESTART    = SCRIPTS_DIR / "restart_watch_loop.ps1"
ALL_F      = [INSTALL, UNINSTALL, STATUS, RESTART]
RUNBOOK    = ROOT / "docs" / "phase5f_scheduler_runbook.md"


# ---------------------------------------------------------------------------
# install_watch_loop_task.ps1
# ---------------------------------------------------------------------------

def _install():
    return INSTALL.read_text(encoding="utf-8")


def test_install_script_pins_mock_envelope_in_script():
    text = _install()
    # The install script itself pins envelope (separate from the task
    # action -- this is about the install-time shell, not the runtime).
    assert re.search(r'\$env:LIVE_TRADING\s+-and\s+\$env:LIVE_TRADING\s+-ne\s+"false"', text)
    assert re.search(r'\$env:EXECUTION_MODE\s+-and\s+\$env:EXECUTION_MODE\s+-ne\s+"mock"', text)
    assert re.search(r'\$env:BROKER_MODE\s+-and\s+\$env:BROKER_MODE\s+-ne\s+"mock"', text)


def test_install_script_refuses_when_envelope_hot():
    text = _install()
    assert re.search(r'Write-Error\s+"Refusing to run:.*LIVE_TRADING', text)


def test_install_script_blacklists_broker_credentials():
    text = _install()
    for cred in ("SHIOAJI_API_KEY", "CTPRO_USER", "IB_PASSWORD"):
        assert cred in text, f"install script blacklist missing {cred}"
    assert re.search(r'Refusing to run:\s*forbidden broker credential', text)


def _install_code_only() -> str:
    """Install script text with comment lines stripped, so guards can
    scan the executable portion without false positives from the
    explanatory header / inline examples."""
    return "\n".join(
        line for line in _install().splitlines()
        if not line.lstrip().startswith("#")
    )


def test_install_script_task_action_pins_envelope():
    """The task Action's command line must explicitly set the mock
    envelope BEFORE invoking the wrapper, so a Task Scheduler GUI
    inspection shows the pin and the wrapper sees it for its own
    refuse-if-hot block."""
    text = _install()
    # The pin is one assignment to $envPin, on one line. Single-quoted
    # 'false' / 'mock' / 'mock'. Backtick-escaped `$ so the outer
    # double-quoted PS string keeps the $env: literal.
    has_pin = re.search(
        r"`\$env:LIVE_TRADING\s*=\s*'false';\s*"
        r"`\$env:EXECUTION_MODE\s*=\s*'mock';\s*"
        r"`\$env:BROKER_MODE\s*=\s*'mock'",
        text,
    )
    assert has_pin, (
        "install script must build a task action that pins LIVE_TRADING / "
        "EXECUTION_MODE / BROKER_MODE in the command line itself"
    )


def test_install_script_registers_in_custom_task_folder():
    text = _install()
    assert '"\\AIFiboVisionTrader\\"' in text, \
        "install script must register tasks under \\AIFiboVisionTrader\\, not root"
    # The Register-ScheduledTask cmdlet uses PowerShell splatting across
    # multiple lines (backtick continuations), so use DOTALL.
    assert re.search(r'Register-ScheduledTask[\s\S]{0,300}-TaskPath', text), \
        "Register-ScheduledTask cmdlet must include -TaskPath"


def test_install_script_creates_tasks_disabled_by_default():
    """Without -EnableNow, every Register call is followed by
    Disable-ScheduledTask."""
    text = _install()
    assert "Disable-ScheduledTask" in text, "install script must call Disable-ScheduledTask"
    # Ensure the disable is in the not-EnableNow branch.
    assert re.search(r'if\s*\(\s*-not\s+\$EnableNow\s*\)[^{]*\{[^}]*Disable-ScheduledTask',
                     text, re.DOTALL), \
        "Disable-ScheduledTask must be inside the `if (-not $EnableNow)` branch"


def test_install_script_does_not_include_submit_by_default():
    """`-Submit` (as a wrapper-invocation flag) must only enter the task
    action via an `if ($Submit) { ... += " -Submit" }` guard. We verify
    BOTH that the guard exists AND that no unconditional `-Submit`
    string remains after we elide the guard."""
    code = _install_code_only()
    cond_pattern = r'if\s*\(\s*\$Submit\s*\)\s*\{[^}]*\+=\s*"\s*-Submit"\s*\}'
    assert re.search(cond_pattern, code), \
        'install script must have `if ($Submit) { $loopExtraArgs += " -Submit" }`'
    # Elide the conditional and any echo-only -Submit references in
    # Write-Host (which are operator messages, not the task action).
    code_without_cond = re.sub(cond_pattern, '', code)
    code_executable = "\n".join(
        line for line in code_without_cond.splitlines()
        if "Write-Host" not in line
    )
    # `[switch]$Submit,` in the param block is fine; it's `$Submit`, not
    # `-Submit`. Same for `$Submit.IsPresent`.
    # Bare `-Submit` (preceded by a non-word, non-$ char) anywhere else
    # would be unconditional.
    bare = re.findall(r'(?<![A-Za-z\$])-Submit\b', code_executable)
    assert not bare, f"install script has unconditional -Submit outside the guard: {bare}"


def test_install_script_does_not_include_live_capture_by_default():
    code = _install_code_only()
    cond_pattern = r'if\s*\(\s*\$LiveCapture\s*\)\s*\{[^}]*\+=\s*"\s*-LiveCapture"\s*\}'
    assert re.search(cond_pattern, code), \
        'install script must have `if ($LiveCapture) { ... += " -LiveCapture" }`'
    code_without_cond = re.sub(cond_pattern, '', code)
    code_executable = "\n".join(
        line for line in code_without_cond.splitlines()
        if "Write-Host" not in line
    )
    bare = re.findall(r'(?<![A-Za-z\$])-LiveCapture\b', code_executable)
    assert not bare, f"install script has unconditional -LiveCapture outside the guard: {bare}"


def test_install_script_refuses_overwrite_without_force():
    text = _install()
    assert re.search(r'if\s*\(\s*-not\s+\$Force\s*\)[^{]*\{[^}]*Write-Error', text, re.DOTALL), \
        "install script must Write-Error when task exists and -Force is absent"


def test_install_script_uses_interactive_principal_not_system():
    text = _install()
    assert "New-ScheduledTaskPrincipal" in text
    assert re.search(r'-LogonType\s+Interactive', text), \
        "Principal must use -LogonType Interactive (not S4U / Password)"
    assert re.search(r'-RunLevel\s+Limited', text), \
        "Principal must use -RunLevel Limited (not Highest)"
    # Hard-deny SYSTEM-style user ids in EXECUTABLE code (the install
    # script's header comment legitimately explains "tasks will NEVER
    # run as LocalSystem"). Strip comment lines before scanning.
    code = _install_code_only()
    for forbidden_user in ("LocalSystem", "NetworkService", "LocalService", "SYSTEM"):
        assert forbidden_user not in code, \
            f"install script must not use {forbidden_user} as principal (in executable code)"


def test_install_script_action_invokes_wrapper_scripts():
    text = _install()
    # The task action must reference the Phase-5.E wrapper paths.
    assert "scripts\\watch_loop_run.ps1" in text or "watch_loop_run.ps1" in text
    assert "scripts\\quote_feed_run.ps1" in text or "quote_feed_run.ps1" in text


def test_install_script_uses_atlogon_trigger():
    text = _install()
    assert "New-ScheduledTaskTrigger" in text
    assert re.search(r'-AtLogOn\b', text), "trigger must be -AtLogOn (current-user logon)"
    assert "-AtStartup" not in text, "must NOT use -AtStartup (would run without user session)"


def test_install_script_does_not_pass_password():
    """`-Password` on Register-ScheduledTask or New-ScheduledTaskPrincipal
    would let the task run without an interactive logon -- and would
    require storing a credential. Forbidden."""
    text = _install()
    assert not re.search(r'(?:Register-ScheduledTask|New-ScheduledTaskPrincipal)[^\n]*-Password\b',
                          text), "install script must not pass -Password"


# ---------------------------------------------------------------------------
# uninstall_watch_loop_task.ps1
# ---------------------------------------------------------------------------

def _uninstall():
    return UNINSTALL.read_text(encoding="utf-8")


def test_uninstall_script_unregisters_both_tasks():
    text = _uninstall()
    # The loop body calls Unregister-ScheduledTask once per name in the
    # @("watch_loop", "quote_feed") collection.
    assert "Unregister-ScheduledTask" in text
    assert re.search(r'@\(\s*"watch_loop"\s*,\s*"quote_feed"\s*\)', text), \
        "uninstall must enumerate both task names"


def test_uninstall_script_uses_confirm_false():
    text = _uninstall()
    assert re.search(r'Unregister-ScheduledTask[^\n]*-Confirm:\$false', text), \
        "Unregister-ScheduledTask must pass -Confirm:$false (no interactive prompt)"


def test_uninstall_script_idempotent_when_tasks_absent():
    text = _uninstall()
    # If Get-ScheduledTask returns nothing, the script reports and continues -- not error.
    assert re.search(r'-ErrorAction\s+SilentlyContinue', text), \
        "uninstall must use SilentlyContinue on the existence check"
    assert "not present" in text, "uninstall must report 'not present' for missing tasks"


def test_uninstall_script_handles_pid_files():
    text = _uninstall()
    assert "watch_loop.pid.log" in text and "quote_feed.pid.log" in text
    # KeepPidFiles is the only way to leave them behind.
    assert "$KeepPidFiles" in text


# ---------------------------------------------------------------------------
# watch_loop_status.ps1
# ---------------------------------------------------------------------------

def _status():
    return STATUS.read_text(encoding="utf-8")


def test_status_script_queries_scheduled_tasks():
    text = _status()
    assert "Get-ScheduledTask" in text
    assert "Get-ScheduledTaskInfo" in text


def test_status_script_audits_trades_jsonl_for_mock_mode():
    text = _status()
    assert "trades.jsonl" in text
    # Must check for non-mock rows.
    assert re.search(r"\$r\.mode\s+-ne\s+'mock'", text) or \
           re.search(r'\$r\.mode\s+-ne\s+"mock"', text), \
        "status script must check that no trades.jsonl row has mode != 'mock'"


def test_status_script_checks_jsonl_freshness():
    text = _status()
    assert "watch_loop.jsonl" in text
    assert "quotes.jsonl" in text
    assert re.search(r'\$r\.ts', text), \
        "status script must read .ts from the last jsonl row for freshness math"


def test_status_script_non_zero_exit_on_non_mock_row():
    text = _status()
    # The INCIDENT branch must exit 1.
    assert re.search(r'INCIDENT[^\n]*\n[^\n]*exit\s+1', text, re.MULTILINE), \
        "status script must exit 1 when an INCIDENT is detected"


def test_status_script_does_not_pin_envelope():
    """The status script is read-only inspection; pinning would mask
    a hot envelope that the operator is trying to detect."""
    text = _status()
    assert not re.search(r'^\s*\$env:LIVE_TRADING\s*=\s*"false"', text, re.MULTILINE), \
        "status script must not pin LIVE_TRADING (it's a read-only check)"


# ---------------------------------------------------------------------------
# restart_watch_loop.ps1
# ---------------------------------------------------------------------------

def _restart():
    return RESTART.read_text(encoding="utf-8")


def test_restart_script_stops_before_starts():
    text = _restart()
    stop_pos = text.find("Stop-ScheduledTask")
    start_pos = text.find("Start-ScheduledTask")
    assert stop_pos > 0 and start_pos > 0, "restart script must call both cmdlets"
    assert stop_pos < start_pos, "Stop-ScheduledTask must precede Start-ScheduledTask in the source"


def test_restart_script_polls_for_stop_completion():
    text = _restart()
    assert "Start-Sleep" in text, "restart script must Start-Sleep between polls"
    assert re.search(r'while\s*\(', text), "restart script must have a poll loop"
    assert "TimeoutSec" in text


def test_restart_script_supports_only_loop_only_feed_switches():
    text = _restart()
    assert "[switch]$OnlyLoop" in text
    assert "[switch]$OnlyFeed" in text


# ---------------------------------------------------------------------------
# Cross-script invariants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", ALL_F)
def test_no_npm_install(script):
    text = script.read_text(encoding="utf-8")
    assert not re.search(r'\bnpm\s+install\b', text), f"{script.name}: must not run npm install"


@pytest.mark.parametrize("script", ALL_F)
def test_no_yolo_or_accept_hooks(script):
    text = script.read_text(encoding="utf-8")
    for pat in [r"--yolo\b", r"--accept-hooks\b",
                r"HERMES_ACCEPT_HOOKS\s*=\s*['\"]?(?:1|true|yes|on)"]:
        assert not re.search(pat, text), f"{script.name}: forbidden token /{pat}/"


@pytest.mark.parametrize("script", ALL_F)
def test_no_live_trading_true_anywhere(script):
    text = script.read_text(encoding="utf-8")
    assert not re.search(
        r"LIVE_TRADING\s*=\s*['\"]?(?:1|true|yes|on)\b",
        text, re.IGNORECASE,
    ), f"{script.name}: must not set LIVE_TRADING truthy"


# ---------------------------------------------------------------------------
# Runbook structure
# ---------------------------------------------------------------------------

def test_runbook_exists():
    assert RUNBOOK.exists(), f"runbook missing: {RUNBOOK}"


def test_runbook_references_each_script():
    text = RUNBOOK.read_text(encoding="utf-8")
    for script in ALL_F:
        assert script.name in text, f"runbook does not mention {script.name}"


def test_runbook_documents_task_install_with_disabled_default():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "Disabled by default" in text or "Disabled" in text, \
        "runbook must document the 'tasks created Disabled by default' contract"
    assert "Enable-ScheduledTask" in text, \
        "runbook must mention Enable-ScheduledTask as the operator's next step"


def test_runbook_documents_eight_layer_mock_only_check():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "8" in text and "獨立守護" in text, \
        "runbook must document the 8 independent layers"
    assert "RiskGate" in text and "trades.jsonl" in text


def test_runbook_has_operator_checklist_section():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert re.search(r'^##\s*8\.\s', text, re.MULTILINE)
    assert "Operator Checklist" in text


def test_runbook_has_uninstall_procedure_section():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert re.search(r'^##\s*6\.\s', text, re.MULTILINE), "runbook missing section ## 6"
    assert "uninstall_watch_loop_task.ps1" in text
