"""Phase-5.C scoped repo guards.

Mirrors test_mock_only.py and test_no_live_trading_phase5b.py but for
the Phase-5.C touched-file set (filtered Fibo line + latest_quote +
chart calibration). Phase 5.C did not add new production source --
the wiring already existed in chart_calibration / mock_fibo_signal /
quote_provider -- but we still enumerate the modules the pipeline
depends on so a future refactor that pulls a broker SDK / network
library into any of them fails loudly here.

Rules guarded:

  1. No broker SDK import in any Phase-5.C-relevant file.
  2. No LIVE_TRADING set to a truthy value.
  3. No Hermes --yolo / --accept-hooks bypass.
  4. No HTTP / network library import in the pipeline modules.
  5. tests/fixtures/capture_calibration_demo.yaml must not be copied
     into config/capture.yaml -- it is a synthetic test fixture, not a
     real chart calibration.
  6. chart_calibration / fibo_line_filter / quote_provider must not
     import executor / risk / strategy. The calibration + filter layer
     is read-only post-processing; the quote layer is read-only data.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PHASE5C_PIPELINE_FILES = [
    ROOT / "vision" / "chart_calibration.py",
    ROOT / "vision" / "fibo_line_filter.py",
    ROOT / "tools"  / "filter_fibo_lines.py",
    ROOT / "tools"  / "mock_fibo_signal.py",
    ROOT / "tools"  / "calibrate_chart.py",
    ROOT / "tools"  / "quote_feed.py",
    ROOT / "quote"  / "quote_provider.py",
]

PHASE5C_NEW_FILES = [
    ROOT / "tests" / "test_phase5c_latest_quote_pipeline.py",
    ROOT / "tests" / "fixtures" / "capture_calibration_demo.yaml",
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

FORBIDDEN_TRADE_ORIGIN_IMPORTS = ["executor", "risk", "strategy"]

DENIAL_RE = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _is_pure_comment(line: str) -> bool:
    s = line.lstrip()
    return s.startswith("#") or s.startswith("//")


@pytest.mark.parametrize("path", PHASE5C_NEW_FILES + PHASE5C_PIPELINE_FILES)
def test_phase5c_file_exists(path):
    assert path.exists(), f"Phase-5.C file missing: {path.relative_to(ROOT)}"


@pytest.mark.parametrize("path", PHASE5C_PIPELINE_FILES)
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


@pytest.mark.parametrize("path", PHASE5C_PIPELINE_FILES)
def test_no_broker_sdk_import(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for sdk in BROKER_SDKS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(sdk)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{sdk}'")
    assert not hits, "Broker SDK import in Phase-5.C pipeline file:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", [
    ROOT / "vision" / "chart_calibration.py",
    ROOT / "vision" / "fibo_line_filter.py",
    ROOT / "quote"  / "quote_provider.py",
    ROOT / "tools"  / "quote_feed.py",
    ROOT / "tools"  / "filter_fibo_lines.py",
])
def test_no_network_library_import_in_read_only_layers(path):
    """Calibration / filter / quote layers are read-only. A network
    library would let a refactor turn the quote feed into a live
    notifier or the filter into a remote scorer without anyone noticing."""
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for lib in NETWORK_LIBS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(lib)}\b", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{lib}'")
    assert not hits, "Network library in read-only layer:\n" + "\n".join(hits)


@pytest.mark.parametrize("path", [
    ROOT / "vision" / "chart_calibration.py",
    ROOT / "vision" / "fibo_line_filter.py",
    ROOT / "quote"  / "quote_provider.py",
    ROOT / "tools"  / "quote_feed.py",
])
def test_read_only_layers_do_not_import_trade_origin_packages(path):
    text = path.read_text(encoding="utf-8")
    hits = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if _is_pure_comment(line) or DENIAL_RE.search(line):
            continue
        for pkg in FORBIDDEN_TRADE_ORIGIN_IMPORTS:
            if re.search(rf"^\s*(?:import|from)\s+{re.escape(pkg)}(\.|$|\s)", line):
                hits.append(f"{path.relative_to(ROOT)}:{lineno}: '{pkg}'")
    assert not hits, "Trade-origin import in read-only layer:\n" + "\n".join(hits)


def test_demo_calibration_fixture_not_copied_into_real_config():
    """If the synthetic test fixture ever shows up in config/capture.yaml
    verbatim, the operator forgot to derive real calibration values from
    a real TradingView screenshot. Fail loudly."""
    cfg = (ROOT / "config" / "capture.yaml").read_text(encoding="utf-8")
    fixture = (ROOT / "tests" / "fixtures" / "capture_calibration_demo.yaml").read_text(encoding="utf-8")

    # Look for the specific anchor numbers from the demo fixture.
    demo_anchors = [
        "pixel_y: 33",
        "price: 2400.0",
        "pixel_y: 2033",
        "price: 2200.0",
    ]
    real_calibration_active = (
        "calibration:" in cfg
        and not all(
            line.lstrip().startswith("#")
            for line in cfg.splitlines()
            if "calibration:" in line
        )
    )
    if not real_calibration_active:
        return  # block still commented out; nothing to enforce
    matched = sum(1 for a in demo_anchors if a in cfg)
    assert matched < len(demo_anchors), (
        "config/capture.yaml carries the synthetic demo anchors verbatim "
        f"({matched}/{len(demo_anchors)} matched). Replace with real values "
        "derived from logs/capture_test.png."
    )
