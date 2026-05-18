"""Phase-4 repo-wide forbidden-pattern scan.

Catches accidental additions to *production* files (under app/,
capture/, vision/, strategy/, risk/, executor/, notify/, scripts/,
configs/) of:

  * LIVE_TRADING set to a truthy value (=true / =1 / =yes / =on)
  * HERMES_ACCEPT_HOOKS set to a truthy value
  * --yolo passed on a Hermes command line
  * --accept-hooks passed on a Hermes command line

These are the four execution-bypass switches that, if turned on,
make every other mock-only guarantee in this repository moot.

Test files (tests/) and operator documentation (docs/) are excluded
because they legitimately reference these patterns as things to
forbid. Within production files, lines that themselves *deny* the
pattern (containing words like "refused", "refuse", "forbidden",
"deny", "never", "禁止", "不准", "must not") and lines that are pure
comments (begin with `#`) are skipped, so guard code that mentions
the forbidden literal in order to reject it does not trigger a
false positive.

The same scan also requires that no production file imports any of
the listed broker SDKs (this duplicates and broadens
``test_no_forbidden_broker_imports`` from ``test_mock_only.py`` so
that ``scripts/`` and ``configs/`` are covered too).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROD_DIRS = [
    "app", "capture", "vision", "strategy", "risk",
    "executor", "notify", "scripts", "configs", "quote",
]

SCAN_EXTS = {
    ".py", ".ps1", ".psm1", ".yaml", ".yml",
    ".bat", ".cmd", ".sh", ".toml", ".ini", ".cfg",
}

DENIAL_MARKERS = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)

FORBIDDEN_PATTERNS = [
    (
        re.compile(
            r"\bLIVE_TRADING\s*[:=]\s*['\"]?(?:1|true|yes|on)\b",
            re.IGNORECASE,
        ),
        "LIVE_TRADING set to truthy",
    ),
    (
        re.compile(
            r"\bHERMES_ACCEPT_HOOKS\s*[:=]\s*['\"]?(?:1|true|yes|on)\b",
            re.IGNORECASE,
        ),
        "HERMES_ACCEPT_HOOKS set to truthy",
    ),
    (re.compile(r"--yolo\b"),         "Hermes --yolo flag"),
    (re.compile(r"--accept-hooks\b"), "Hermes --accept-hooks flag"),
]

BROKER_SDKS = [
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]

PY_IMPORT_PATTERNS = [
    (
        re.compile(rf"^\s*(?:from|import)\s+{re.escape(sdk)}\b"),
        f"Python import of broker SDK '{sdk}'",
    )
    for sdk in BROKER_SDKS
]


def _scanned_files():
    for d in PROD_DIRS:
        root = PROJECT_ROOT / d
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in SCAN_EXTS:
                continue
            if any(part in {".git", "__pycache__", ".venv"} for part in p.parts):
                continue
            yield p


def _is_pure_comment(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("#") or stripped.startswith("//")


@pytest.mark.parametrize("pattern,description", FORBIDDEN_PATTERNS)
def test_execution_bypass_pattern_absent_from_production(pattern, description):
    hits = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if _is_pure_comment(line):
                continue
            if DENIAL_MARKERS.search(line):
                continue
            if pattern.search(line):
                rel = path.relative_to(PROJECT_ROOT)
                hits.append(f"  {rel}:{lineno}: {line.strip()}")
    assert not hits, (
        f"Forbidden execution-bypass pattern detected ({description}). "
        "This is a Phase-4 safety guard; "
        "see docs/hermes_operator_runbook.md §7.\n" + "\n".join(hits)
    )


@pytest.mark.parametrize("pattern,description", PY_IMPORT_PATTERNS)
def test_broker_sdk_import_absent_from_production(pattern, description):
    hits = []
    for path in _scanned_files():
        if path.suffix.lower() != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                rel = path.relative_to(PROJECT_ROOT)
                hits.append(f"  {rel}:{lineno}: {line.strip()}")
    assert not hits, (
        f"{description} detected in a production file. "
        "Mock-only build forbids broker SDK imports anywhere outside "
        "tests/.\n" + "\n".join(hits)
    )
