"""Phase-5 scoped repo guards.

The repo-wide scan in ``test_no_hermes_yolo.py`` already enforces the
"no execution bypass" rules across all production directories. This
file re-asserts the same rules with an *explicit enumeration* of the
files Phase 5 added or changed, so a future grep / refactor that
accidentally drops Phase-5 files out of the wider scan would still
fail loudly here.

Patterns guarded (per Phase-5 spec):

  1. No broker SDK import in the Phase-5 file set.
  2. No ``LIVE_TRADING`` set to a truthy value.
  3. No ``--yolo`` / ``--accept-hooks`` on a Hermes command line.
  4. No ``HERMES_ACCEPT_HOOKS`` set to a truthy value.

Plus a stricter rule that only applies to the new signal generator:
  5. ``tools/mock_fibo_signal.py`` must not import any HTTP / network
     library — its only sinks are local files via the existing
     MockExecutor.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE5_FILES = [
    ROOT / "tools"   / "detect_fibo_lines.py",
    ROOT / "tools"   / "mock_fibo_signal.py",
    ROOT / "vision"  / "fibo_line_detector.py",
    ROOT / "notify"  / "telegram_bot.py",
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
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]

NETWORK_LIBS_FORBIDDEN_IN_MOCK_SIGNAL = [
    "requests", "httpx", "aiohttp", "urllib3", "socket", "urllib.request",
]

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#") or s.startswith("//")


@pytest.mark.parametrize("path", PHASE5_FILES)
def test_phase5_file_exists(path):
    assert path.exists(), f"Phase-5 file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE5_FILES)
def test_no_execution_bypass_in_phase5_file(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(
                    f"{path.relative_to(ROOT)}:{lineno} [{desc}]: {line.strip()}"
                )
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", PHASE5_FILES)
def test_no_broker_sdk_import_in_phase5_file(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for sdk in BROKER_SDKS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(sdk)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: import of '{sdk}'")
    assert not hits, "Broker SDK import in Phase-5 file:\n" + "\n".join(hits)


def test_mock_fibo_signal_imports_no_network_libraries():
    """Hard rule: the signal generator's only sinks are local files,
    routed through MockExecutor. Importing requests / httpx / aiohttp /
    urllib3 / raw socket would let a future refactor turn it into a
    live notifier without anyone noticing.
    """
    path = ROOT / "tools" / "mock_fibo_signal.py"
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for lib in NETWORK_LIBS_FORBIDDEN_IN_MOCK_SIGNAL:
            pat = rf"^\s*(?:import|from)\s+{re.escape(lib)}\b"
            if re.search(pat, line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{lib}'")
    assert not hits, "Network library imported in mock_fibo_signal:\n" + "\n".join(hits)
