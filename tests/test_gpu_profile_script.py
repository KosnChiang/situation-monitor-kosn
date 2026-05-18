"""Static checks for scripts/gpu_profile.ps1.

The GPU profile script is a read-only inspection tool. These tests
verify its safety properties (no env writes, no process kills, no
file modifications) and its functional shape (queries nvidia-smi,
supports -Strict, exits 2 on drift) without running it -- because
running it in pytest would couple test outcomes to whatever happens
to be on the operator's GPU at test time.
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

GPU_PROFILE = ROOT / "scripts" / "gpu_profile.ps1"


def _gpu_profile() -> str:
    return GPU_PROFILE.read_text(encoding="utf-8")


def _gpu_profile_code_only() -> str:
    """Strip comment-only lines so static checks ignore the header."""
    return "\n".join(
        line for line in _gpu_profile().splitlines()
        if not line.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Existence + parameter shape
# ---------------------------------------------------------------------------

def test_gpu_profile_script_exists():
    assert GPU_PROFILE.exists(), f"missing: {GPU_PROFILE}"


def test_gpu_profile_declares_strict_switch():
    text = _gpu_profile()
    assert re.search(r'\[switch\]\$Strict', text), \
        "gpu_profile.ps1 must declare `[switch]$Strict`"


# ---------------------------------------------------------------------------
# Read-only invariants
# ---------------------------------------------------------------------------

def test_gpu_profile_does_not_pin_mock_envelope():
    """Read-only inspection MUST NOT mask a hot envelope. A strict
    operator running this script wants to see the env as-is, not
    a script-imposed override."""
    code = _gpu_profile_code_only()
    assert not re.search(r'^\s*\$env:LIVE_TRADING\s*=\s*"false"', code, re.MULTILINE), \
        "gpu_profile.ps1 must not pin LIVE_TRADING"
    assert not re.search(r'^\s*\$env:EXECUTION_MODE\s*=\s*"mock"', code, re.MULTILINE), \
        "gpu_profile.ps1 must not pin EXECUTION_MODE"
    assert not re.search(r'^\s*\$env:BROKER_MODE\s*=\s*"mock"', code, re.MULTILINE), \
        "gpu_profile.ps1 must not pin BROKER_MODE"
    assert not re.search(r'\$env:CUDA_VISIBLE_DEVICES\s*=\s*', code), \
        "gpu_profile.ps1 must READ but not WRITE CUDA_VISIBLE_DEVICES"


def test_gpu_profile_does_not_modify_processes_or_tasks():
    """Read-only: no Stop-Process, no Register/Unregister-ScheduledTask,
    no Start-Process, no New-Service."""
    code = _gpu_profile_code_only()
    forbidden = [
        r'\bStop-Process\b',
        r'\bStart-Process\b',
        r'\bRegister-ScheduledTask\b',
        r'\bUnregister-ScheduledTask\b',
        r'\bStop-ScheduledTask\b',
        r'\bStart-ScheduledTask\b',
        r'\bEnable-ScheduledTask\b',
        r'\bDisable-ScheduledTask\b',
        r'\bNew-Service\b',
        r'\bSet-Service\b',
        r'\bRestart-Service\b',
    ]
    for pat in forbidden:
        assert not re.search(pat, code), f"gpu_profile.ps1 must not call /{pat}/"


def test_gpu_profile_does_not_touch_files():
    """Read-only: no Remove-Item, no Set-Content / Out-File / Add-Content,
    no New-Item with -ItemType File, no Copy-Item, no Move-Item."""
    code = _gpu_profile_code_only()
    forbidden = [
        r'\bRemove-Item\b',
        r'\bSet-Content\b',
        r'\bAdd-Content\b',
        r'\bOut-File\b',
        r'\bCopy-Item\b',
        r'\bMove-Item\b',
        r'\bNew-Item\b',
    ]
    for pat in forbidden:
        assert not re.search(pat, code), f"gpu_profile.ps1 must not call /{pat}/"


def test_gpu_profile_does_not_invoke_python_or_modify_repo():
    """Read-only: no python invocation, no git mutations, no scheduled-task
    folder cleanup via COM. The script may REFERENCE the string
    'python.exe' (it filters Get-CimInstance by process name to identify
    project python processes); what it must not do is actually CALL python."""
    code = _gpu_profile_code_only()
    # Real invocations: `& python ...`, `python -m ...`, `python.exe -m ...`
    invocation_patterns = [
        r'&\s+[\'"]?[^\s\'"]*python(?:\.exe)?[\'"]?\s+-m\b',
        r'(?:^|[\s;])python(?:\.exe)?\s+-m\b',
    ]
    for pat in invocation_patterns:
        assert not re.search(pat, code), \
            f"gpu_profile.ps1 must not invoke python: /{pat}/"
    # Git / npm mutations and COM scheduler folder cleanup remain forbidden.
    for pat in [r'\bgit\s+(?:add|commit|push|reset|checkout)\b',
                r'Schedule\.Service',
                r'\bnpm\s+install\b']:
        assert not re.search(pat, code), f"gpu_profile.ps1 must not contain /{pat}/"


# ---------------------------------------------------------------------------
# Functional shape
# ---------------------------------------------------------------------------

def test_gpu_profile_queries_nvidia_smi_per_gpu_summary():
    code = _gpu_profile_code_only()
    assert re.search(r'nvidia-smi\s+--query-gpu=', code), \
        "gpu_profile.ps1 must invoke `nvidia-smi --query-gpu=`"
    # Required fields for the per-GPU summary.
    for field in ("memory.total", "memory.used", "memory.free",
                  "utilization.gpu", "temperature.gpu", "power.draw",
                  "display_active"):
        assert field in code, f"per-GPU query must include {field}"


def test_gpu_profile_queries_nvidia_smi_compute_apps():
    code = _gpu_profile_code_only()
    assert re.search(r'nvidia-smi\s+--query-compute-apps=', code), \
        "gpu_profile.ps1 must invoke `nvidia-smi --query-compute-apps=` to list processes"
    for field in ("pid", "process_name", "gpu_uuid"):
        assert field in code, f"compute-apps query must include {field}"


def test_gpu_profile_resolves_uuid_to_gpu_index():
    code = _gpu_profile_code_only()
    assert re.search(r'\$indexByUuid', code), \
        "gpu_profile.ps1 must map gpu_uuid -> index to bucket compute processes per GPU"
    assert re.search(r'nvidia-smi\s+--query-gpu=index,uuid', code)


def test_gpu_profile_supports_strict_drift_judgment():
    code = _gpu_profile_code_only()
    assert re.search(r'if\s*\(\s*\$Strict\s*\)', code), \
        "gpu_profile.ps1 must branch on `if ($Strict)` for strict checks"
    # Must enumerate the 4 health checks documented in the proposal.
    assert "display_active" in code
    assert re.search(r"ollama", code, re.IGNORECASE)
    assert re.search(r"watch_fibo_loop|tools\\\.", code)
    # And must publish DRIFT verdict + exit 2.
    assert "DRIFT" in code
    assert re.search(r'exit\s+2\b', code), "strict drift must exit 2"


def test_gpu_profile_supports_ok_exit_in_strict_when_no_drift():
    code = _gpu_profile_code_only()
    # Must have an exit 0 path inside the Strict block (no drift case).
    assert re.search(
        r'OVERALL\s*:\s*OK[^\n]*partition matches[^\n]*\n[^\n]*exit\s+0',
        code,
    ), "strict no-drift path must report OK + exit 0"


def test_gpu_profile_non_strict_exits_zero_without_drift_verdict():
    code = _gpu_profile_code_only()
    # The else branch (no -Strict) must print PROFILE complete + exit 0.
    assert re.search(
        r'PROFILE complete[\s\S]{0,200}exit\s+0',
        code,
    ), "non-strict path must print 'PROFILE complete' and exit 0"


def test_gpu_profile_handles_missing_nvidia_smi_gracefully():
    code = _gpu_profile_code_only()
    assert "nvidia-smi unavailable" in code, \
        "gpu_profile.ps1 must report cleanly when nvidia-smi returns nothing"
    # And must exit (rather than continue and blow up parsing).
    m = re.search(r'nvidia-smi unavailable[\s\S]{0,400}', code)
    assert m and re.search(r'exit\s+0', m.group(0)), \
        "the nvidia-smi-unavailable branch must early-exit 0"


# ---------------------------------------------------------------------------
# Cross-script invariants (mock-only family)
# ---------------------------------------------------------------------------

def test_gpu_profile_no_yolo_or_accept_hooks():
    text = _gpu_profile()
    for pat in [r'--yolo\b', r'--accept-hooks\b',
                r'HERMES_ACCEPT_HOOKS\s*=\s*["\']?(?:1|true|yes|on)']:
        assert not re.search(pat, text), f"forbidden token in gpu_profile.ps1: /{pat}/"


def test_gpu_profile_no_live_trading_truthy():
    text = _gpu_profile()
    assert not re.search(
        r"LIVE_TRADING\s*=\s*['\"]?(?:1|true|yes|on)\b",
        text, re.IGNORECASE,
    )


def test_gpu_profile_no_broker_credential_env_assignment():
    """Read-only: must not assign any well-known broker credential env."""
    code = _gpu_profile_code_only()
    for cred in ("SHIOAJI_API_KEY", "IB_PASSWORD", "MT5_LOGIN",
                 "BINANCE_API_KEY", "ALPACA_API_KEY", "CTPRO_USER"):
        assert not re.search(rf'\$env:{cred}\s*=', code), \
            f"gpu_profile.ps1 must not assign {cred}"
