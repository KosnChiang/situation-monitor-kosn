"""GPU profile scoped repo guards.

Mirrors tests/test_no_live_trading_phase5*.py for the GPU profile
tool: 1 PowerShell script + 2 test files. Re-asserts the mock-only
rules with explicit file enumeration so a future refactor that drops
a GPU-profile file out of the wider scan would still fail loudly
here.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

GPU_PROFILE_SCRIPT = ROOT / "scripts" / "gpu_profile.ps1"
GPU_PROFILE_FILES = [
    GPU_PROFILE_SCRIPT,
    ROOT / "tests" / "test_gpu_profile_script.py",
    ROOT / "tests" / "test_no_live_trading_gpu_profile.py",
]

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


@pytest.mark.parametrize("path", GPU_PROFILE_FILES)
def test_gpu_profile_file_exists(path):
    assert path.exists(), f"GPU-profile file missing: {path.relative_to(ROOT)}"


def test_gpu_profile_script_no_execution_bypass():
    text = GPU_PROFILE_SCRIPT.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"gpu_profile.ps1:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


def test_gpu_profile_script_no_broker_sdk_token():
    text = GPU_PROFILE_SCRIPT.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment_or_denial(line):
            continue
        for sdk in BROKER_SDKS:
            if sdk in line:
                hits.append(f"gpu_profile.ps1:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK token in gpu_profile.ps1:\n" + "\n".join(hits)


def test_gpu_profile_script_no_npm_install():
    text = GPU_PROFILE_SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r'\bnpm\s+install\b', text)


def test_gpu_profile_script_does_not_modify_state():
    """Defence-in-depth on the read-only invariant. Independent of
    tests/test_gpu_profile_script.py which scans the same patterns;
    this restates the contract from the no-live-trading lens."""
    text = GPU_PROFILE_SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    # The whole script must not write the envelope envs.
    assert not re.search(r'\$env:LIVE_TRADING\s*=\s*', code)
    assert not re.search(r'\$env:EXECUTION_MODE\s*=\s*', code)
    assert not re.search(r'\$env:BROKER_MODE\s*=\s*', code)
    assert not re.search(r'\$env:CUDA_VISIBLE_DEVICES\s*=\s*', code)
    # No process termination from a "read-only" script.
    for verb in ("Stop-Process", "Stop-ScheduledTask", "Unregister-ScheduledTask",
                 "Remove-Item", "Set-Content"):
        assert verb not in code, f"gpu_profile.ps1 must not call {verb}"


def test_gpu_profile_script_does_not_read_broker_credentials():
    """The script may inspect env names for diagnosis but must not
    READ values of broker credential envs (which would risk logging
    them). Pattern: `[System.Environment]::GetEnvironmentVariable(...)` /
    `$env:SHIOAJI_API_KEY` etc."""
    text = GPU_PROFILE_SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith("#")
    )
    for cred in ("SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
                 "IB_ACCOUNT", "IB_USERNAME", "IB_PASSWORD",
                 "MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER",
                 "BINANCE_API_KEY", "BINANCE_API_SECRET",
                 "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
                 "CTPRO_USER", "CTPRO_PASSWORD", "CTPRO_TOKEN"):
        assert cred not in code, \
            f"gpu_profile.ps1 must not reference broker credential env '{cred}'"
