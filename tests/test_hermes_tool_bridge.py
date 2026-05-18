"""Tests for the Hermes tool-bridge fix scaffold.

This file verifies the three artefacts added to address the
"tool-call JSON emitted as plain text" failure mode documented in
docs/hermes_tool_bridge_runbook.md §2:

    1. AGENTS.md exists at the repo root and contains the mock-only
       envelope and the explicit "use the tool-call channel, do not
       emit JSON as text" instruction.
    2. docs/hermes_tool_bridge_runbook.md exists and contains the
       expected sections referenced from AGENTS.md and the smoke
       script.
    3. scripts/hermes_tool_bridge_smoke.ps1 exists, is non-empty,
       and does NOT pass --yolo / --accept-hooks / HERMES_ACCEPT_HOOKS
       to hermes, and does not import or reference any broker SDK.

The companion broader doc, docs/hermes_training_profile.md (the
project training pack), is owned by a different commit and verified
by other tests; we do not re-assert its structure here.

These are static structural assertions. The actual smoke run happens
on the Windows host via:

    .\\scripts\\hermes_tool_bridge_smoke.ps1

and is out of scope for this Linux-side test suite.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

AGENTS_MD                 = ROOT / "AGENTS.md"
TOOL_BRIDGE_RUNBOOK_MD    = ROOT / "docs" / "hermes_tool_bridge_runbook.md"
SMOKE_SCRIPT_PS1          = ROOT / "scripts" / "hermes_tool_bridge_smoke.ps1"

BROKER_SDKS = [
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]

DENIAL_MARKERS = re.compile(
    r"\b(refus(?:e|es|ed|ing)|reject(?:s|ed|ing)?|deny|denies|denied|"
    r"forbid(?:s|den)?|forbidden|never|must\s*not|do\s*not|"
    r"not\s+allowed|wrong)\b|禁止|不准|拒絕|拒绝",
    re.IGNORECASE,
)


def _read(path: Path) -> str:
    assert path.exists(), f"missing: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------ presence

def test_agents_md_present_at_repo_root():
    assert AGENTS_MD.exists(), "AGENTS.md must live at repo root for Hermes to auto-inject it"
    assert AGENTS_MD.stat().st_size > 1000, "AGENTS.md looks suspiciously short"


def test_tool_bridge_runbook_present_under_docs():
    assert TOOL_BRIDGE_RUNBOOK_MD.exists(), \
        "docs/hermes_tool_bridge_runbook.md must exist; AGENTS.md points at it"
    assert TOOL_BRIDGE_RUNBOOK_MD.stat().st_size > 2000


def test_smoke_script_present_under_scripts():
    assert SMOKE_SCRIPT_PS1.exists()
    assert SMOKE_SCRIPT_PS1.stat().st_size > 500


# ------------------------------------------------------------ AGENTS.md content

def test_agents_md_pins_mock_only_envelope():
    text = _read(AGENTS_MD)
    assert re.search(r"LIVE_TRADING.*MUST be.*false", text), \
        "AGENTS.md must declare LIVE_TRADING must be false"
    assert re.search(r"EXECUTION_MODE.*MUST be.*mock", text), \
        "AGENTS.md must declare EXECUTION_MODE must be mock"
    assert re.search(r"BROKER_MODE.*MUST be.*mock", text), \
        "AGENTS.md must declare BROKER_MODE must be mock"


def test_agents_md_forbids_broker_sdks_by_name():
    text = _read(AGENTS_MD)
    # Every SDK we ban anywhere else in the repo must also appear by name
    # in the agent's system prompt so the model knows what is forbidden.
    for sdk in BROKER_SDKS:
        assert sdk in text, f"AGENTS.md must name '{sdk}' in the forbidden list"


def test_agents_md_teaches_tool_channel_use():
    text = _read(AGENTS_MD)
    assert "tool_calls" in text, "AGENTS.md must mention the tool_calls channel"
    assert re.search(r"WRONG", text, re.IGNORECASE), \
        "AGENTS.md must show a WRONG example of the failure mode"
    assert re.search(r"RIGHT", text, re.IGNORECASE), \
        "AGENTS.md must show a RIGHT example of correct tool use"
    assert "fabricate" in text.lower() or "fake" in text.lower(), \
        "AGENTS.md must explicitly forbid fabricating tool calls"


# ------------------------------------------------------------ tool-bridge runbook content

@pytest.mark.parametrize("heading", [
    "Role",
    "Known failure mode",
    "Three workarounds",
    "Verification",
])
def test_tool_bridge_runbook_has_section(heading):
    text = _read(TOOL_BRIDGE_RUNBOOK_MD)
    assert re.search(rf"^##.*{re.escape(heading)}", text, re.MULTILINE), \
        f"docs/hermes_tool_bridge_runbook.md missing section: {heading}"


def test_tool_bridge_runbook_references_smoke_script():
    text = _read(TOOL_BRIDGE_RUNBOOK_MD)
    assert "hermes_tool_bridge_smoke.ps1" in text, \
        "Tool-bridge runbook must reference the smoke script"


def test_tool_bridge_runbook_explains_qwen_ollama_root_cause():
    text = _read(TOOL_BRIDGE_RUNBOOK_MD).lower()
    assert "qwen" in text and "ollama" in text, \
        "Tool-bridge runbook must explain the qwen2.5 + Ollama bridge interaction"


# ------------------------------------------------------------ smoke-script safety

def test_smoke_script_does_not_pass_yolo_or_accept_hooks():
    text = _read(SMOKE_SCRIPT_PS1)
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if DENIAL_MARKERS.search(line):
            continue
        assert "--yolo" not in line, \
            f"smoke script must not pass --yolo (line {lineno}: {stripped})"
        assert "--accept-hooks" not in line, \
            f"smoke script must not pass --accept-hooks (line {lineno}: {stripped})"
        assert "HERMES_ACCEPT_HOOKS" not in line or "=" not in line.split("HERMES_ACCEPT_HOOKS", 1)[1][:3], \
            f"smoke script must not set HERMES_ACCEPT_HOOKS (line {lineno}: {stripped})"


def test_smoke_script_does_not_reference_broker_sdks():
    text = _read(SMOKE_SCRIPT_PS1)
    for sdk in BROKER_SDKS:
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if DENIAL_MARKERS.search(line):
                continue
            assert sdk not in line, \
                f"smoke script must not reference broker SDK '{sdk}' (line {lineno})"


def test_smoke_script_preflights_mock_envelope():
    text = _read(SMOKE_SCRIPT_PS1)
    assert "LIVE_TRADING" in text and "EXECUTION_MODE" in text and "BROKER_MODE" in text, \
        "smoke script must check all three mock-mode env vars before running"
    # Must abort (exit 2 is our convention) if any is wrong.
    assert re.search(r"exit\s+2", text), \
        "smoke script must use exit 2 on pre-flight abort"


def test_smoke_script_uses_oneshot_mode():
    text = _read(SMOKE_SCRIPT_PS1)
    # `-z` appears as `"-z"` (PowerShell argument list literal); word
    # boundary only on the trailing side.
    assert re.search(r'-z\b', text), \
        "smoke script must use `hermes -z` one-shot mode for pipe-safe execution"


def test_smoke_script_detects_json_leak():
    """The whole point of the smoke is to catch JSON-as-text. Verify
    the script has heuristics for the common leak patterns."""
    text = _read(SMOKE_SCRIPT_PS1)
    assert "tool_calls" in text, "smoke must look for 'tool_calls' string in reply"
    assert "tool_call" in text, "smoke must look for <tool_call> tag in reply"
    assert "read_file" in text, "smoke must look for raw read_file() function name in reply"
