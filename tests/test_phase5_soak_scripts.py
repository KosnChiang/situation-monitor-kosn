"""Static checks for scripts/watch_loop_soak.ps1.

The orchestrator wraps the existing Phase 5.D/5.5 tools without
modifying them. These tests verify the soak script's safety
properties so a future refactor cannot drop them without flipping
the suite red.
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

SOAK = ROOT / "scripts" / "watch_loop_soak.ps1"


def _soak() -> str:
    return SOAK.read_text(encoding="utf-8")


def _soak_code_only() -> str:
    """Soak script with comment lines stripped."""
    return "\n".join(
        line for line in _soak().splitlines()
        if not line.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Envelope safety
# ---------------------------------------------------------------------------

def test_soak_script_pins_mock_envelope():
    text = _soak()
    assert re.search(r'\$env:LIVE_TRADING\s*=\s*"false"', text)
    assert re.search(r'\$env:EXECUTION_MODE\s*=\s*"mock"', text)
    assert re.search(r'\$env:BROKER_MODE\s*=\s*"mock"', text)


def test_soak_script_refuses_when_envelope_hot():
    text = _soak()
    assert re.search(r'\$env:LIVE_TRADING\s+-and\s+\$env:LIVE_TRADING\s+-ne\s+"false"', text)
    assert re.search(r'\$env:EXECUTION_MODE\s+-and\s+\$env:EXECUTION_MODE\s+-ne\s+"mock"', text)
    assert re.search(r'\$env:BROKER_MODE\s+-and\s+\$env:BROKER_MODE\s+-ne\s+"mock"', text)
    assert re.search(r'Write-Error\s+"Refusing to run', text)


def test_soak_script_blacklists_broker_credentials():
    text = _soak()
    for cred in ("SHIOAJI_API_KEY", "CTPRO_USER", "IB_PASSWORD", "MT5_LOGIN"):
        assert cred in text, f"soak script blacklist missing {cred}"
    assert re.search(r'Refusing to run:\s*forbidden broker credential', text)


# ---------------------------------------------------------------------------
# Isolated I/O paths (no pollution of production logs)
# ---------------------------------------------------------------------------

def test_soak_script_writes_to_isolated_paths_only():
    code = _soak_code_only()
    # Required: the four isolated paths must be assigned exactly once each.
    for var, expected in [
        (r'\$soakQuotes\s*=\s*"logs\\quotes_soak\.jsonl"',     "logs/quotes_soak.jsonl"),
        (r'\$soakWatch\s*=\s*"logs\\watch_loop_soak\.jsonl"', "logs/watch_loop_soak.jsonl"),
        (r'\$soakTrades\s*=\s*"logs\\trades_soak\.jsonl"',     "logs/trades_soak.jsonl"),
        (r'\$soakSnaps\s*=\s*"logs\\soak_snapshots\.jsonl"',  "logs/soak_snapshots.jsonl"),
    ]:
        assert re.search(var, code), f"soak script must assign isolated path: {expected}"


def test_soak_script_does_not_write_to_production_logs():
    """No string in the executable code may pass logs/quotes.jsonl /
    logs/watch_loop.jsonl / logs/trades.jsonl as a python --output
    or --watch-log or --quotes-file arg."""
    code = _soak_code_only()
    # Allowed: read-only listing in the post-run sanity Write-Host.
    # Forbidden: passing these as the python tool's output / log arg.
    forbidden = [
        r'--output\s+["\'\$]?logs\\quotes\.jsonl',
        r'--quotes-file\s+["\'\$]?logs\\quotes\.jsonl',
        r'--watch-log\s+["\'\$]?logs\\watch_loop\.jsonl',
        r'--trades-log\s+["\'\$]?logs\\trades\.jsonl',
        r'\$env:TRADES_LOG\s*=\s*["\']?logs\\trades\.jsonl["\']?\s*$',
    ]
    for pat in forbidden:
        assert not re.search(pat, code, re.MULTILINE), \
            f"soak script writes to production log via pattern /{pat}/"


def test_soak_script_redirects_trades_log_env_to_isolated_path():
    code = _soak_code_only()
    assert re.search(r'\$env:TRADES_LOG\s*=\s*\$soakTrades', code), \
        "soak script must set $env:TRADES_LOG to the isolated $soakTrades path"


def test_soak_script_wipes_only_isolated_artifacts():
    """The pre-run Remove-Item should target the *_soak files, NEVER
    the production logs."""
    code = _soak_code_only()
    # Look for the Remove-Item loop input.
    m = re.search(
        r'foreach\s*\(\s*\$p\s+in\s+@\(([^)]+)\)\s*\)\s*\{\s*if\s*\(Test-Path\s+\$p\)\s*\{\s*Remove-Item\s+\$p',
        code,
    )
    assert m, "expected a foreach Remove-Item loop targeting only soak artifacts"
    paths = m.group(1)
    # Each path enumerated must be a soak-only variable.
    soak_vars = {"$soakQuotes", "$soakWatch", "$soakTrades", "$soakSnaps", "$soakJobsLog"}
    for var in re.findall(r'\$soak\w+', paths):
        assert var in soak_vars, f"Remove-Item loop references unknown soak var {var}"
    # And critically: NO production-log literal in the wipe list.
    for prod in ("logs\\quotes.jsonl", "logs\\watch_loop.jsonl", "logs\\trades.jsonl"):
        assert prod not in paths, f"wipe list includes production log {prod}"


# ---------------------------------------------------------------------------
# Submit opt-in
# ---------------------------------------------------------------------------

def test_soak_script_does_not_include_submit_by_default():
    """`--submit` must only enter the loop's job args via an
    `if ($submit)` guard inside the loop Start-Job. Similarly the
    analyzer's `--submit` flag must only be appended inside
    `if ($Submit)` in the post-job analyzer call."""
    code = _soak_code_only()
    # 1. Loop job args: search for the conditional append `if ($submit) { $pyArgs += "--submit" }`.
    assert re.search(r'if\s*\(\s*\$submit\s*\)\s*\{\s*\$pyArgs\s*\+=\s*"--submit"\s*\}',
                     code, re.IGNORECASE), \
        "soak loop job must append --submit only inside `if ($submit)`"
    # 2. Analyzer args: --submit appended only inside `if ($Submit)`.
    assert re.search(r'if\s*\(\s*\$Submit\s*\)\s*\{\s*\$analyzeArgs\s*\+=\s*@\("--submit"',
                     code), \
        "soak analyzer call must append --submit only inside `if ($Submit)`"


def test_soak_script_does_not_include_submit_unconditionally():
    """After eliding the conditional appends, no bare --submit may
    remain in the executable code."""
    code = _soak_code_only()
    # Drop the loop-job conditional.
    code = re.sub(
        r'if\s*\(\s*\$submit\s*\)\s*\{\s*\$pyArgs\s*\+=\s*"--submit"\s*\}',
        '', code, flags=re.IGNORECASE,
    )
    # Drop the analyzer conditional (multi-line list append).
    code = re.sub(
        r'if\s*\(\s*\$Submit\s*\)\s*\{[^}]*\}',
        '', code,
    )
    # Drop Write-Host echo lines (operator messages, not args to tools).
    code = "\n".join(
        line for line in code.splitlines() if "Write-Host" not in line
    )
    bare = re.findall(r'(?<![A-Za-z])--submit\b', code)
    assert not bare, f"unconditional --submit outside the guards: {bare}"


# ---------------------------------------------------------------------------
# Auto-stop via --max-iterations
# ---------------------------------------------------------------------------

def test_soak_script_uses_max_iterations_for_auto_stop():
    code = _soak_code_only()
    # quote feed job and watch loop job each pass --max-iterations.
    assert code.count("--max-iterations") >= 2, \
        "soak script must pass --max-iterations to BOTH quote_feed and watch_fibo_loop"
    # And the values come from computed integers (not 0 / infinite).
    assert re.search(r'\$quoteMax\s*=\s*\[int\]', code)
    assert re.search(r'\$loopMax\s*=\s*\[int\]', code)


# ---------------------------------------------------------------------------
# Analyzer invocation
# ---------------------------------------------------------------------------

def test_soak_script_invokes_analyzer_module():
    code = _soak_code_only()
    assert re.search(r'tools\.analyze_soak', code), \
        "soak script must invoke `tools.analyze_soak`"


def test_soak_script_propagates_analyzer_exit_code():
    code = _soak_code_only()
    assert re.search(r'\$analyzerExit\s*=\s*\$LASTEXITCODE', code)
    assert re.search(r'^exit\s+\$analyzerExit', code, re.MULTILINE), \
        "soak script must `exit $analyzerExit` so PASS/FAIL/DEGRADED propagates"


# ---------------------------------------------------------------------------
# Snapshot loop
# ---------------------------------------------------------------------------

def test_soak_script_appends_one_snapshot_per_interval():
    code = _soak_code_only()
    assert "Add-Content" in code and "$soakSnaps" in code
    # Snapshot interval is a parameter.
    assert re.search(r'\$SnapshotIntervalSec', code)


def test_soak_script_drains_job_buffers_to_jobs_log():
    code = _soak_code_only()
    assert "Receive-Job" in code
    assert "$soakJobsLog" in code


def test_soak_script_finds_pid_via_command_line_match():
    """Start-Job's $PID is the powershell wrapper, not python. The
    soak script must discover the python PIDs via cmdline regex."""
    code = _soak_code_only()
    assert "Get-CimInstance" in code and "Win32_Process" in code
    assert re.search(r'CommandLine\s+-match', code)


# ---------------------------------------------------------------------------
# Cross-script invariants
# ---------------------------------------------------------------------------

def test_soak_script_does_not_npm_install():
    text = _soak()
    assert not re.search(r'\bnpm\s+install\b', text)


def test_soak_script_no_yolo_or_accept_hooks():
    text = _soak()
    for pat in [r'--yolo\b', r'--accept-hooks\b',
                r'HERMES_ACCEPT_HOOKS\s*=\s*["\']?(?:1|true|yes|on)']:
        assert not re.search(pat, text), f"forbidden token in soak script: /{pat}/"


def test_soak_script_no_live_trading_truthy():
    text = _soak()
    assert not re.search(
        r"LIVE_TRADING\s*=\s*['\"]?(?:1|true|yes|on)\b",
        text, re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Sanity: post-run sanity block exists
# ---------------------------------------------------------------------------

def test_soak_script_prints_production_log_sanity_block():
    """After the soak, the script lists the production logs' size and
    last-write timestamp so the operator can confirm at a glance that
    nothing was touched."""
    text = _soak()
    assert "production logs sanity" in text or "production logs" in text
    for prod in ("logs\\quotes.jsonl", "logs\\watch_loop.jsonl", "logs\\trades.jsonl"):
        assert prod in text, f"sanity block must mention {prod}"
