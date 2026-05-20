"""Phase 7.C structural safety guards.

Pins:
  * Phase 7.B-1 streaming probe + Phase 7.C simulation order probe both exist
    under templates/shioaji_live_adapter/.
  * Neither probe imports the main repo (no from/import for app, ai_swing,
    live, quote, tools, strategy, risk, executor, notify, vision, capture).
  * Neither probe imports shioaji at module top -- the SDK must be lazy.
  * Each probe enforces SHIOAJI_SIMULATION truthy at runtime and hard-pins
    sj.Shioaji(simulation=True).
  * simulation_order_probe defaults to DRY-RUN -- --confirm-simulation-submit
    is required to call api.place_order.
  * streaming_quote_probe never references place_order / submit_order.
  * templates/shioaji_live_adapter/requirements.txt still lists shioaji +
    keyring -- the new probes introduce no new mandatory deps.
  * Main repo source does NOT import or reference the new probe modules.
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


TEMPLATE_DIR = ROOT / "templates" / "shioaji_live_adapter"
PHASE7C_FILES = [
    TEMPLATE_DIR / "streaming_quote_probe.py",
    TEMPLATE_DIR / "simulation_order_probe.py",
]

# Main-repo packages that template scripts must NOT import. Each package's
# top-level dir lives at the repo root; we forbid both `from <pkg>` and
# `import <pkg>` forms. (`from live_adapter import ...` is NOT forbidden
# because live_adapter.py is a sibling file inside the template dir, not a
# main-repo `live.` import.)
MAIN_REPO_PKGS = (
    "app", "ai_swing", "live", "quote", "tools", "strategy", "risk",
    "executor", "notify", "vision", "capture",
)


def test_all_phase7c_files_exist():
    for p in PHASE7C_FILES:
        assert p.exists(), f"missing: {p.relative_to(ROOT)}"


@pytest.mark.parametrize("probe_path", PHASE7C_FILES,
                         ids=lambda p: p.name)
def test_no_top_level_shioaji_import(probe_path):
    src = probe_path.read_text(encoding="utf-8")
    pat = re.compile(
        r"^\s*import\s+shioaji\b|^\s*from\s+shioaji\b", re.MULTILINE,
    )
    for match in pat.finditer(src):
        line_start = src.rfind("\n", 0, match.start()) + 1
        line_end = src.find("\n", match.end())
        line = src[line_start:line_end if line_end != -1 else len(src)]
        assert line.startswith((" ", "\t")), (
            f"{probe_path.name}: top-level shioaji import: {line!r}"
        )


@pytest.mark.parametrize("probe_path", PHASE7C_FILES,
                         ids=lambda p: p.name)
def test_no_main_repo_imports(probe_path):
    src = probe_path.read_text(encoding="utf-8")
    offenders: list[str] = []
    for pkg in MAIN_REPO_PKGS:
        # `from <pkg>.x import y` or `from <pkg> import y`
        from_pat = re.compile(
            rf"^\s*from\s+{pkg}(?:\.|\s+import\b)", re.MULTILINE,
        )
        # `import <pkg>` or `import <pkg>.x`
        imp_pat = re.compile(
            rf"^\s*import\s+{pkg}(?:\.|\s|$)", re.MULTILINE,
        )
        if from_pat.search(src):
            offenders.append(f"from {pkg}...")
        if imp_pat.search(src):
            offenders.append(f"import {pkg}")
    assert not offenders, (
        f"{probe_path.name}: forbidden main-repo imports: {offenders}"
    )


@pytest.mark.parametrize("probe_path", PHASE7C_FILES,
                         ids=lambda p: p.name)
def test_probe_refuses_when_simulation_not_truthy(probe_path):
    src = probe_path.read_text(encoding="utf-8")
    assert "SHIOAJI_SIMULATION" in src, (
        f"{probe_path.name}: no SHIOAJI_SIMULATION reference"
    )
    assert re.search(r"REFUSE.*SHIOAJI_SIMULATION", src), (
        f"{probe_path.name}: missing REFUSE message tied to SHIOAJI_SIMULATION"
    )


@pytest.mark.parametrize("probe_path", PHASE7C_FILES,
                         ids=lambda p: p.name)
def test_probe_pins_simulation_true_at_construct(probe_path):
    src = probe_path.read_text(encoding="utf-8")
    assert re.search(r"sj\.Shioaji\(\s*simulation\s*=\s*True\s*\)", src), (
        f"{probe_path.name}: must construct Shioaji with simulation=True "
        "hard-pinned"
    )


def test_simulation_order_probe_defaults_to_dry_run():
    src = (TEMPLATE_DIR / "simulation_order_probe.py").read_text(
        encoding="utf-8",
    )
    assert "--confirm-simulation-submit" in src
    assert re.search(
        r"--confirm-simulation-submit[\"'][^)]*?"
        r"action\s*=\s*[\"']store_true[\"']",
        src, re.DOTALL,
    ), "--confirm-simulation-submit must be store_true so default is False"
    call_match = re.search(r"\bapi\.place_order\s*\(", src)
    guard_match = re.search(r"\bargs\.confirm_simulation_submit\b", src)
    assert call_match is not None, "probe must call api.place_order(...)"
    assert guard_match is not None, (
        "probe must read args.confirm_simulation_submit before the call"
    )
    assert guard_match.start() < call_match.start(), (
        "args.confirm_simulation_submit must appear in source before "
        "api.place_order(...) so the call is gated by the flag"
    )


def test_streaming_probe_does_not_place_orders():
    src = (TEMPLATE_DIR / "streaming_quote_probe.py").read_text(
        encoding="utf-8",
    )
    # Function-call form so docstrings mentioning the names do not trip the
    # check.
    for forbidden in ("place_order", "submit_order"):
        pat = re.compile(rf"\b{forbidden}\s*\(")
        assert not pat.search(src), (
            f"streaming probe must not call {forbidden}(...) -- read-only"
        )


def test_template_requirements_unchanged_minimum():
    """The new probes must not introduce new mandatory deps beyond what
    Phase 7.A already required (shioaji + keyring)."""
    reqs = (TEMPLATE_DIR / "requirements.txt").read_text(
        encoding="utf-8",
    ).lower()
    assert "shioaji" in reqs
    assert "keyring" in reqs


def test_main_repo_does_not_reference_new_probes():
    """No main-repo Python file may import or name the probe modules."""
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules", "__pycache__",
        ".pytest_cache", "tests", "docs", "templates",
    }
    forbidden = ("streaming_quote_probe", "simulation_order_probe")
    offenders: list[str] = []
    for p in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for needle in forbidden:
            if needle in text:
                offenders.append(
                    f"{p.relative_to(ROOT)} references {needle}"
                )
    assert not offenders, offenders
