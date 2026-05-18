"""Phase-5.D scoped repo guards.

The watch loop orchestrates trade-origin packages (executor / risk /
strategy) on the --submit path and a network library (requests, via
TelegramBot) on the --notify-mode live path. Both must be loaded
**lazily** so the default invocation of the loop pulls in nothing
that can place an order or open a socket.

Rules guarded:

  1. tools/watch_fibo_loop.py exists and is reachable.
  2. No LIVE_TRADING set to a truthy value, no Hermes bypass flag.
  3. No broker SDK import anywhere in the file.
  4. No HTTP / network library import anywhere in the file.
  5. ``executor`` / ``risk`` / ``strategy`` are NEVER imported at
     module top -- only inside the --submit code path.
  6. ``mss`` and ``capture.screen_capture`` are NEVER imported at
     module top -- only inside the --live-capture code path.
  7. ``notify.telegram_bot`` is NEVER imported at module top -- only
     inside the --notify-mode != off code path.
  8. ``quote.quote_provider`` is not imported at all -- the loop
     consumes ``logs/quotes.jsonl`` produced by the separate
     ``tools.quote_feed`` process.

The AST-based check on rules 5-8 is what makes "submit is opt-in" and
"offline is mss-free" structurally enforceable rather than just
documented.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE5D_FILES = [
    ROOT / "tools" / "watch_fibo_loop.py",
]

PHASE5D_TEST_AND_FIXTURE_FILES = [
    ROOT / "tests" / "test_watch_fibo_loop.py",
    ROOT / "tests" / "fixtures" / "watch_loop_calibration_demo.yaml",
    ROOT / "tests" / "fixtures" / "watch_loop_row_schema.json",
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

FORBIDDEN_TOP_LEVEL_ROOTS = {
    "executor", "risk", "strategy",
    "mss", "capture",
    "notify",
}

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#") or s.startswith("//")


@pytest.mark.parametrize("path", PHASE5D_FILES + PHASE5D_TEST_AND_FIXTURE_FILES)
def test_phase5d_file_exists(path):
    assert path.exists(), f"Phase-5.D file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE5D_FILES)
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


@pytest.mark.parametrize("path", PHASE5D_FILES)
def test_no_broker_sdk_import(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for sdk in BROKER_SDKS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(sdk)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK import in Phase-5.D file:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", PHASE5D_FILES)
def test_no_network_library_import(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for lib in NETWORK_LIBS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{lib}'")
    assert not hits, "Network library in Phase-5.D file:\n" + "\n".join(hits)


def _module_top_imports(path: Path) -> list[tuple[str, int]]:
    """Return [(root_module, lineno)] for top-level import statements only.

    Imports inside function / class bodies are deliberately ignored;
    those are the "lazy" path the loop uses for opt-in behaviour.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for n in node.names:
                out.append((n.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod:
                out.append((mod, node.lineno))
    return out


def test_loop_module_top_does_not_import_trade_origin_or_capture_or_notify():
    path = ROOT / "tools" / "watch_fibo_loop.py"
    tops = _module_top_imports(path)
    hits = [
        f"line {ln}: top-level import of '{mod}'"
        for mod, ln in tops
        if mod in FORBIDDEN_TOP_LEVEL_ROOTS
    ]
    assert not hits, (
        "Phase-5.D contract: these roots must be lazy-imported only:\n"
        + "\n".join(hits)
    )


def test_loop_does_not_import_quote_provider_at_all():
    """The watch loop consumes ``logs/quotes.jsonl`` via tail-read.
    Importing quote.quote_provider would re-create a quote source
    inside the order-execution process and break the Phase-5.5
    process-boundary contract."""
    text = (ROOT / "tools" / "watch_fibo_loop.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|from)\s+quote\b", text, re.MULTILINE), (
        "watch_fibo_loop must not import quote.* anywhere"
    )


def test_loop_lazy_imports_are_actually_present():
    """Sanity guard: confirm the loop *does* have the lazy imports we
    claim to enforce, so this guard file doesn't drift past a refactor
    that quietly removed them. We expect to find:
      * `from executor.mock_executor import MockExecutor`
      * `from risk.risk_gate import RiskGate`
      * `from strategy.fibo_mob_v2 import Signal as StrategySignal`
      * `from capture.screen_capture import`
      * `from notify.telegram_bot import TelegramBot`
    inside (not at top of) the file. We assert each substring exists;
    the AST check above guarantees it's not at top level.
    """
    text = (ROOT / "tools" / "watch_fibo_loop.py").read_text(encoding="utf-8")
    expected = [
        "from executor.mock_executor import MockExecutor",
        "from risk.risk_gate import RiskGate",
        "from strategy.fibo_mob_v2 import Signal as StrategySignal",
        "from capture.screen_capture import",
        "from notify.telegram_bot import TelegramBot",
    ]
    missing = [s for s in expected if s not in text]
    assert not missing, (
        "Expected lazy imports missing from watch_fibo_loop.py:\n  "
        + "\n  ".join(missing)
    )
