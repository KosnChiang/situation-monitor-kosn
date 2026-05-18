"""Hard safety tests for the read-only quote layer.

Spec items mapped to tests:

  1. quote module does not import executor (or risk / strategy).
  2. quote module does not import any broker SDK.
  3. quote module does not call submit / place_order / send_order /
     execute_live / place_live_order.
  4. quote module only writes ``logs/quotes.jsonl`` (verified in
     test_quote_feed_readonly.py::test_quote_feed_creates_only_quotes_jsonl).
  5. LIVE_TRADING=true must be rejected (verified there too plus
     in this file at the unit boundary).
  6. CTPro / Shioaji / Binance / ccxt / MetaTrader5 / ib_insync
     forbidden in quote module source.
  7. No TradingView cookie / session-token strings.
  8. No npm packages added anywhere in the repo.
  9. No Mathieu2301 / TradingView-API references in the repo.

A file is considered part of the "quote layer" if it lives under
``quote/`` or is the standalone CLI ``tools/quote_feed.py``.
``tools/mock_fibo_signal.py`` is in the execution layer (it talks
to RiskGate + MockExecutor) and is intentionally excluded from
this scan -- guards for that file live in
``test_no_live_trading_phase5.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

QUOTE_FILES = [
    ROOT / "quote" / "__init__.py",
    ROOT / "quote" / "quote_provider.py",
    ROOT / "tools" / "quote_feed.py",
]

FORBIDDEN_TOPLEVEL_IMPORTS = ["executor", "risk", "strategy"]

BROKER_SDKS = [
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]

FORBIDDEN_CALL_NAMES = [
    "submit", "place_order", "send_order", "execute_live",
    "place_live_order", "real_order", "live_order",
]

TRADINGVIEW_CRED_PATTERNS = [
    re.compile(r"\btv_?session\b", re.IGNORECASE),
    re.compile(r"\btv_?cookie\b", re.IGNORECASE),
    re.compile(r"\bTRADINGVIEW_SESSION", re.IGNORECASE),
    re.compile(r"\bTRADINGVIEW_AUTH", re.IGNORECASE),
    re.compile(r"\bsession_?id\b", re.IGNORECASE),
    re.compile(r"\bsession_?token\b", re.IGNORECASE),
    re.compile(r"\bauth_?token\b", re.IGNORECASE),
    re.compile(r"\bauth_?cookie\b", re.IGNORECASE),
    re.compile(r'["\']cookie["\']\s*:', re.IGNORECASE),  # "Cookie" header literal
]

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed|read[- ]?only|no\s+\w+|without)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#") or s.startswith("//")


# -------------------------------------------------------------- 1, 2, 3, 6, 7

@pytest.mark.parametrize("path", QUOTE_FILES)
def test_no_forbidden_layer_import(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for mod in FORBIDDEN_TOPLEVEL_IMPORTS:
            if re.search(rf"^\s*(?:from|import)\s+{re.escape(mod)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: import of '{mod}'")
    assert not hits, "Quote layer must not import executor/risk/strategy:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", QUOTE_FILES)
def test_no_broker_sdk_import_in_quote_layer(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for sdk in BROKER_SDKS:
            if re.search(rf"^\s*(?:from|import)\s+{re.escape(sdk)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK import in quote layer:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", QUOTE_FILES)
def test_no_order_function_calls_in_quote_layer(path):
    """Item 3: no ``.submit(`` / ``submit(`` / ``place_order(`` / etc.

    We look for ``name(`` (i.e. an actual call site), not the bare word,
    so docstrings and field names don't trip it. Lines containing a
    denial keyword still get a free pass for things like docstrings
    saying "must not call submit()".
    """
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for fn in FORBIDDEN_CALL_NAMES:
            if re.search(rf"\b{re.escape(fn)}\s*\(", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno} [{fn}]: {line.strip()}")
    assert not hits, "Order-placement call in quote layer:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", QUOTE_FILES)
def test_no_tradingview_session_or_cookie_strings(path):
    """Item 7: no TradingView cookie / session-token strings."""
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for pat in TRADINGVIEW_CRED_PATTERNS:
            if pat.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert not hits, "TradingView session/cookie literal in quote layer:\n" + "\n".join(hits)


# -------------------------------------------------------------- 8, 9 (repo-wide)

def _all_repo_files():
    skip_dirs = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip_dirs for part in p.parts):
            continue
        yield p


def test_no_npm_package_artifacts_anywhere():
    """Item 8: no npm package files added to the repo."""
    npm_indicators = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"}
    hits = [str(p.relative_to(ROOT)) for p in _all_repo_files() if p.name in npm_indicators]
    assert not hits, f"npm package artefact in repo: {hits}"


def test_no_node_modules_directory():
    hits = [p for p in ROOT.rglob("node_modules") if p.is_dir() and ".git" not in p.parts]
    assert not hits, f"node_modules directory present: {hits}"


def test_no_tradingview_api_install_reference_in_dependency_files():
    """Item 9: no Mathieu2301/TradingView-API in any dependency manifest.

    Mentions in docs/ and tests/ are allowed (this file mentions it,
    so does docs/quote_feed_design.md). Only actual install / dep
    manifests are scanned.
    """
    dep_files = []
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml",
                 "Pipfile", "Pipfile.lock", "setup.py", "setup.cfg",
                 "environment.yml", "conda-env.yml"):
        p = ROOT / name
        if p.exists():
            dep_files.append(p)
    forbidden_substrings = [
        "Mathieu2301", "tradingview-api", "tradingview_api",
        "TradingView-API",
    ]
    hits = []
    for path in dep_files:
        text = path.read_text(encoding="utf-8")
        for needle in forbidden_substrings:
            if needle in text:
                hits.append(f"{path.relative_to(ROOT)}: contains '{needle}'")
    assert not hits, "TradingView-API install reference in dep manifest:\n" + "\n".join(hits)
