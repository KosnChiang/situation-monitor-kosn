"""Static checks for scripts/calibrate_chart_dry_run.ps1.

The wizard wraps the existing tools.calibrate_chart CLI. Its only
job beyond the wrapped tool is operator UX: prompt for 4 numbers,
stage the proposed YAML to a gitignored log, show a read-only diff
of config/capture.yaml, and open the overlay PNG. These tests pin
the safety properties that matter:

  * never writes config/capture.yaml
  * stages to logs/calibration_proposal.log (gitignored as *.log)
  * refuses on hot envelope / broker credential
  * delegates the actual calibration math to tools.calibrate_chart
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

WIZARD = ROOT / "scripts" / "calibrate_chart_dry_run.ps1"
RUNBOOK = ROOT / "docs" / "live_capture_calibration_runbook.md"


def _wizard() -> str:
    return WIZARD.read_text(encoding="utf-8")


def _wizard_code_only() -> str:
    return "\n".join(
        line for line in _wizard().splitlines()
        if not line.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# Existence + parameter shape
# ---------------------------------------------------------------------------

def test_wizard_exists():
    assert WIZARD.exists(), f"missing: {WIZARD}"


def test_runbook_exists():
    assert RUNBOOK.exists(), f"missing: {RUNBOOK}"


def test_wizard_declares_four_reference_point_params():
    text = _wizard()
    for p in (r'\[int\]\$PixelYHigh',
              r'\[double\]\$PriceHigh',
              r'\[int\]\$PixelYLow',
              r'\[double\]\$PriceLow'):
        assert re.search(p, text), f"wizard must declare {p}"


def test_wizard_declares_interactive_switch():
    text = _wizard()
    assert re.search(r'\[switch\]\$Interactive', text)


def test_wizard_declares_no_open_viewer_switch():
    text = _wizard()
    assert re.search(r'\[switch\]\$NoOpenViewer', text)


# ---------------------------------------------------------------------------
# CRITICAL: must not write config/capture.yaml
# ---------------------------------------------------------------------------

CONFIG_PATH_PATTERNS = [
    r'config\\capture\.yaml',
    r'config/capture\.yaml',
]


def test_wizard_does_not_write_config_capture_yaml():
    """The whole point of LC5a: wizard NEVER modifies config/capture.yaml.
    Operator's final step is a manual paste, so the wizard must not
    Set-Content / Out-File / Add-Content / write-redirect / WriteAllText
    against that path."""
    code = _wizard_code_only()
    forbidden = []
    for cfg_pat in CONFIG_PATH_PATTERNS:
        forbidden += [
            rf'Set-Content[^\n]*{cfg_pat}',
            rf'Add-Content[^\n]*{cfg_pat}',
            rf'Out-File[^\n]*{cfg_pat}',
            rf'>\s*[\'"]?{cfg_pat}',
            rf'>>\s*[\'"]?{cfg_pat}',
            rf'\[IO\.File\]::WriteAllText\([^\n]*{cfg_pat}',
            rf'\[System\.IO\.File\]::WriteAllText\([^\n]*{cfg_pat}',
            rf'New-Item[^\n]*{cfg_pat}[^\n]*-Force',
        ]
    hits = [pat for pat in forbidden if re.search(pat, code)]
    assert not hits, f"wizard must NOT write config/capture.yaml. Hits: {hits}"


def test_wizard_references_config_capture_yaml_only_for_reading_or_displaying():
    """Allowed references to config/capture.yaml: param default value,
    Get-Content (in the diff helper, either direct or via a helper
    function called with $Config), Test-Path, error messages, and
    user-visible echo strings. Forbidden: any write call (covered by
    the test above)."""
    code = _wizard_code_only()
    # At least one read-style use must exist (the diff helper). Accept
    # either a direct `Get-Content $Config` / hard-coded path, OR the
    # helper-function pattern `Get-CommentedCalibrationBlock -Path $Config`
    # (which delegates to Get-Content inside the helper body).
    read_patterns = [
        r'Get-Content[^\n]*\$Config',
        r'Get-Content[^\n]*config\\capture\.yaml',
        r'Get-CommentedCalibrationBlock[^\n]*-Path\s+\$Config',
        r'Get-CommentedCalibrationBlock[^\n]*\$Config',
    ]
    assert any(re.search(p, code) for p in read_patterns), \
        "wizard must read config/capture.yaml at least once (for the diff helper)"
    # And the Get-CommentedCalibrationBlock helper itself must read via Get-Content.
    helper_body = re.search(
        r'function\s+Get-CommentedCalibrationBlock\s*\{[\s\S]*?^\}',
        code, re.MULTILINE,
    )
    if helper_body:
        assert "Get-Content" in helper_body.group(0), \
            "Get-CommentedCalibrationBlock helper must read via Get-Content"


def test_wizard_stages_proposal_to_log_extension():
    """LC3b: proposed YAML is staged to logs/calibration_proposal.log,
    which lands under the existing logs/*.log gitignore rule."""
    code = _wizard_code_only()
    assert re.search(r'\$ProposalLog\s*=\s*"logs\\calibration_proposal\.log"', code), \
        "wizard must default $ProposalLog = 'logs\\calibration_proposal.log'"
    # And the staged path must end in .log (so the gitignore covers it).
    assert ".pid.log" not in code, "calibration proposal must NOT use the PID-file extension"


def test_proposal_log_path_is_gitignored_by_existing_rule():
    """Defence: 'logs/*.log' must already exist in .gitignore, so the
    staged proposal never gets accidentally committed."""
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "logs/*.log" in gi, ".gitignore must keep 'logs/*.log' so the staged proposal is ignored"


# ---------------------------------------------------------------------------
# Envelope safety
# ---------------------------------------------------------------------------

def test_wizard_pins_mock_envelope():
    text = _wizard()
    assert re.search(r'\$env:LIVE_TRADING\s*=\s*"false"', text)
    assert re.search(r'\$env:EXECUTION_MODE\s*=\s*"mock"', text)
    assert re.search(r'\$env:BROKER_MODE\s*=\s*"mock"', text)


def test_wizard_refuses_when_envelope_hot():
    text = _wizard()
    assert re.search(r'\$env:LIVE_TRADING\s+-and\s+\$env:LIVE_TRADING\s+-ne\s+"false"', text)
    assert re.search(r'\$env:EXECUTION_MODE\s+-and\s+\$env:EXECUTION_MODE\s+-ne\s+"mock"', text)
    assert re.search(r'\$env:BROKER_MODE\s+-and\s+\$env:BROKER_MODE\s+-ne\s+"mock"', text)
    assert re.search(r'Write-Error\s+"Refusing to run', text)


def test_wizard_blacklists_broker_credentials():
    text = _wizard()
    for cred in ("SHIOAJI_API_KEY", "CTPRO_USER", "IB_PASSWORD", "MT5_LOGIN"):
        assert cred in text, f"wizard cred blacklist missing {cred}"


# ---------------------------------------------------------------------------
# Functional shape
# ---------------------------------------------------------------------------

def test_wizard_invokes_existing_calibrate_chart_module():
    code = _wizard_code_only()
    assert "tools.calibrate_chart" in code, \
        "wizard must delegate calibration to the existing tools.calibrate_chart CLI"
    # And it must pass the 4 reference-point flags.
    for flag in ("--pixel-y-high", "--price-high",
                 "--pixel-y-low", "--price-low",
                 "--input", "--output", "--tick"):
        assert flag in code, f"wizard must pass {flag} to tools.calibrate_chart"


def test_wizard_local_axis_check_runs_before_python():
    """A friendly axis-direction check inside PowerShell so the operator
    gets a fast 'axis_inverted' error rather than waiting for python +
    seeing a CalibrationError traceback."""
    code = _wizard_code_only()
    assert re.search(r'function\s+Test-AxisDirection', code) or \
           re.search(r'axis_inverted', code), \
        "wizard must perform an early axis-direction check"


def test_wizard_extracts_yaml_block_from_python_stdout():
    code = _wizard_code_only()
    assert re.search(r'IndexOf\([\'"]calibration:[\'"]\)', code), \
        "wizard must locate the 'calibration:' block in python stdout to stage"


def test_wizard_shows_before_after_diff():
    """Operators paste manually; the diff helper minimises paste-into-
    wrong-place errors. The wizard reads config/capture.yaml in a
    'Get-CommentedCalibrationBlock' helper and prints both sides."""
    code = _wizard_code_only()
    assert "Get-CommentedCalibrationBlock" in code, \
        "wizard must expose a Get-CommentedCalibrationBlock helper"
    assert re.search(r'before/after diff', code, re.IGNORECASE)


def test_wizard_opens_overlay_png_by_default():
    code = _wizard_code_only()
    assert "Invoke-Item" in code, \
        "wizard must open overlay PNG via Invoke-Item (default Windows viewer)"
    assert "$NoOpenViewer" in code, \
        "wizard must support a -NoOpenViewer switch for headless / CI runs"


def test_wizard_emits_next_steps_block():
    code = _wizard_code_only()
    assert re.search(r'next steps', code, re.IGNORECASE)
    # The next steps must mention manual paste + ChartCalibration validation.
    assert "ChartCalibration" in code, \
        "wizard's next-steps block must reference ChartCalibration.from_yaml validation"
    assert "watch_loop_smoke" in code, \
        "wizard's next-steps block must reference watch_loop_smoke for regression"


# ---------------------------------------------------------------------------
# Cross-script invariants (mock-only family)
# ---------------------------------------------------------------------------

def test_wizard_no_yolo_or_accept_hooks():
    text = _wizard()
    for pat in [r'--yolo\b', r'--accept-hooks\b',
                r'HERMES_ACCEPT_HOOKS\s*=\s*["\']?(?:1|true|yes|on)']:
        assert not re.search(pat, text), f"forbidden token in wizard: /{pat}/"


def test_wizard_no_live_trading_truthy():
    text = _wizard()
    assert not re.search(
        r"LIVE_TRADING\s*=\s*['\"]?(?:1|true|yes|on)\b",
        text, re.IGNORECASE,
    )


def test_wizard_no_npm_install():
    text = _wizard()
    assert not re.search(r'\bnpm\s+install\b', text)


def test_wizard_no_broker_sdk_token():
    text = _wizard()
    for sdk in ("ib_insync", "ibapi", "MetaTrader5", "ccxt.",
                "binance.", "alpaca_trade_api", "oandapyV20"):
        assert sdk not in text, f"forbidden broker SDK token in wizard: {sdk!r}"


# ---------------------------------------------------------------------------
# Runbook structure
# ---------------------------------------------------------------------------

def test_runbook_references_wizard_and_existing_tools():
    text = RUNBOOK.read_text(encoding="utf-8")
    for ref in ("calibrate_chart_dry_run.ps1",
                "tools.calibrate_chart",
                "tools.capture_test",
                "ChartCalibration",
                "vision/chart_calibration.py"):
        assert ref in text, f"runbook missing reference to {ref}"


def test_runbook_documents_manual_paste_step():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "config/capture.yaml" in text or "config\\capture.yaml" in text
    # The runbook must explicitly say the operator manually pastes.
    assert "manually paste" in text.lower() or "manual paste" in text.lower() or "手動" in text


def test_runbook_documents_three_layer_validation():
    text = RUNBOOK.read_text(encoding="utf-8")
    # Layer headings should appear in the runbook.
    for hdr in ("視覺驗證", "結構驗證", "端對端驗證"):
        assert hdr in text, f"runbook missing layer heading: {hdr}"


def test_runbook_has_operator_checklist_section():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert re.search(r'^##\s*8\.\s', text, re.MULTILINE), \
        "runbook missing section ## 8"
    assert "Operator Checklist" in text or "Checklist" in text


def test_runbook_has_rollback_path():
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "git checkout -- config/capture.yaml" in text or "git checkout -- config\\capture.yaml" in text, \
        "runbook must document the rollback path"
