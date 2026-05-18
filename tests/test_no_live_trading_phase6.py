"""Phase-6 forbidden-pattern scan.

Repo-wide scans already exist in test_no_hermes_yolo.py /
test_quote_feed_no_execution.py / earlier phase tests. This file
re-asserts the same invariants explicitly against the four files
added or modified by Phase 6, so a future refactor that drops these
files out of the broader scope still fails loudly here:

  * vision/fibo_line_level_namer.py           (NEW namer)
  * vision/fibo_detector.py                   (added try_detect)
  * tools/mock_fibo_signal.py                 (--strategy + --no-telegram)
  * tools/watch_fibo_loop.py                  (--strategy)

Patterns guarded:
  1. No broker SDK import.
  2. No LIVE_TRADING set to truthy.
  3. No HERMES_ACCEPT_HOOKS set to truthy.
  4. No --yolo / --accept-hooks anywhere.
  5. New namer must not import torch / ultralytics directly
     (YOLO comes in only through an injected callable).
  6. New namer must not import notify / executor / risk / quote
     (it is pure CV-level metadata, no execution side effects).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE6_FILES = [
    ROOT / "vision" / "fibo_line_level_namer.py",
    ROOT / "vision" / "fibo_detector.py",
    ROOT / "tools"  / "mock_fibo_signal.py",
    ROOT / "tools"  / "watch_fibo_loop.py",
]

BROKER_SDKS = [
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]

EXECUTION_BYPASS = [
    (re.compile(r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
     "LIVE_TRADING truthy"),
    (re.compile(r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE),
     "HERMES_ACCEPT_HOOKS truthy"),
    (re.compile(r"--yolo\b"),         "--yolo flag"),
    (re.compile(r"--accept-hooks\b"), "--accept-hooks flag"),
]

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed|wrong)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#") or s.startswith("//")


@pytest.mark.parametrize("path", PHASE6_FILES)
def test_phase6_file_exists(path):
    assert path.exists(), f"Phase-6 file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE6_FILES)
def test_no_broker_sdk_import_in_phase6_file(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for sdk in BROKER_SDKS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(sdk)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: import of '{sdk}'")
    assert not hits, "Broker SDK import in Phase-6 file:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", PHASE6_FILES)
def test_no_execution_bypass_in_phase6_file(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for pattern, desc in EXECUTION_BYPASS:
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno} [{desc}]: {line.strip()}")
    assert not hits, "Execution-bypass pattern in Phase-6 file:\n" + "\n".join(hits)


def test_namer_does_not_import_torch_or_ultralytics():
    """The whole point of the injected-callable design is that the
    namer itself never depends on torch / ultralytics. If a future
    refactor adds those imports, the dependency surface grows and the
    "graceful degradation when YOLO is unavailable" property breaks."""
    text = (ROOT / "vision" / "fibo_line_level_namer.py").read_text(encoding="utf-8")
    forbidden = ["torch", "ultralytics"]
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for lib in forbidden:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", line):
                hits.append(f"line {lineno}: '{lib}'")
    assert not hits, "Namer must not import torch/ultralytics:\n" + "\n".join(hits)


def test_namer_does_not_import_execution_side_modules():
    """The namer is pure CV metadata. Importing notify / executor /
    risk / quote here would be a layering violation -- the Phase-6
    pipeline must thread those through the *receivers* (mock_fibo_signal
    / watch_fibo_loop), not through the namer."""
    text = (ROOT / "vision" / "fibo_line_level_namer.py").read_text(encoding="utf-8")
    forbidden = ["notify", "executor", "risk", "quote", "app"]
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for mod in forbidden:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(mod)}(\.|\s)", line):
                hits.append(f"line {lineno}: '{mod}'")
    assert not hits, "Namer must not import execution-side modules:\n" + "\n".join(hits)


def test_mock_fibo_signal_telegram_notify_is_dry_safe():
    """Sanity: the new _notify_telegram helper in mock_fibo_signal
    must never raise out of process. Static check: it must be
    wrapped in try/except OR call TelegramBot which has its own
    safe defaults."""
    text = (ROOT / "tools" / "mock_fibo_signal.py").read_text(encoding="utf-8")
    assert "_notify_telegram" in text, "Phase-6 must add _notify_telegram helper"
    # The helper itself contains a try/except (verified by reading,
    # but we encode it as a regex check too).
    helper = re.search(r"def _notify_telegram.*?(?=^def |\Z)", text, re.DOTALL | re.MULTILINE)
    assert helper, "Could not locate _notify_telegram body"
    assert "try:" in helper.group(0) and "except" in helper.group(0), \
        "_notify_telegram body must wrap the Telegram call in try/except"
