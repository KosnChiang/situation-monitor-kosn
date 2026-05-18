"""Phase-5.B scoped repo guards.

Mirrors the structure of tests/test_mock_only.py but for the Phase-5.B
file set (Fibo-line filtering). Re-asserts the mock-only rules with an
explicit enumeration so that a refactor which moves these files out of
the wider scan still fails loudly here.

Rules guarded:

  1. No broker SDK import in any Phase-5.B file.
  2. No ``LIVE_TRADING`` set to a truthy value.
  3. No Hermes ``--yolo`` / ``--accept-hooks`` bypass.
  4. No HTTP / network library import (the filter is a pure transform).
  5. ``vision/fibo_line_filter.py`` must not import ``executor.*``,
     ``risk.*`` or ``quote.*`` -- it is the read-only post-processing
     layer, never a trade origin.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE5B_FILES = [
    ROOT / "vision" / "fibo_line_filter.py",
    ROOT / "tools"  / "filter_fibo_lines.py",
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

NETWORK_LIBS = ["requests", "httpx", "aiohttp", "urllib3", "socket", "urllib.request"]

FORBIDDEN_TRADE_ORIGIN_IMPORTS = ["executor", "risk", "quote"]

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#") or s.startswith("//")


@pytest.mark.parametrize("path", PHASE5B_FILES)
def test_phase5b_file_exists(path):
    assert path.exists(), f"Phase-5.B file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE5B_FILES)
def test_no_execution_bypass(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for pattern, desc in FORBIDDEN_EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Forbidden execution-bypass pattern:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", PHASE5B_FILES)
def test_no_broker_sdk_import(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for sdk in BROKER_SDKS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(sdk)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK import in Phase-5.B file:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", PHASE5B_FILES)
def test_no_network_library_import(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for lib in NETWORK_LIBS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{lib}'")
    assert not hits, "Network library in Phase-5.B file:\n" + "\n".join(hits)


def test_filter_module_does_not_touch_trade_origin_packages():
    """The filter is post-processing. It must never import
    executor / risk / quote -- those are trade-origin packages and
    a transitive import would let a future refactor accidentally
    place orders from the filter layer."""
    text = (ROOT / "vision" / "fibo_line_filter.py").read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for pkg in FORBIDDEN_TRADE_ORIGIN_IMPORTS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(pkg)}(\.|$|\s)", line):
                hits.append(f"vision/fibo_line_filter.py:{lineno}: '{pkg}'")
    assert not hits, "Trade-origin package import in filter:\n" + "\n".join(hits)
