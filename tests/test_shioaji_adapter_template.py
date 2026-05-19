"""Structural tests for the Shioaji adapter TEMPLATE (Phase 7.A).

The template lives at templates/shioaji_live_adapter/ and is meant to
be COPIED OUT of the main repo. These tests check the template's
shape but do NOT execute it (shioaji is not installed).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


TEMPLATE_DIR = ROOT / "templates" / "shioaji_live_adapter"
TEMPLATE_ADAPTER = TEMPLATE_DIR / "live_adapter.py"
TEMPLATE_README = TEMPLATE_DIR / "README.md"
TEMPLATE_REQS = TEMPLATE_DIR / "requirements.txt"


def test_template_dir_exists():
    assert TEMPLATE_DIR.is_dir()


def test_template_files_exist():
    assert TEMPLATE_ADAPTER.exists()
    assert TEMPLATE_README.exists()
    assert TEMPLATE_REQS.exists()


def test_template_defines_class_liveadapter():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "class LiveAdapter:" in src


def test_template_imports_shioaji_inside_methods_only():
    """The template must use lazy imports for shioaji so the file
    loads (e.g. for linting) without the SDK installed. No top-level
    'import shioaji'."""
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "import shioaji" in src
    # Make sure shioaji isn't imported at the module top:
    top_pattern = re.compile(r"^\s*import\s+shioaji\b|^\s*from\s+shioaji\b", re.MULTILINE)
    for match in top_pattern.finditer(src):
        line_start = src.rfind("\n", 0, match.start()) + 1
        # If the line has any leading whitespace, it's indented (i.e.
        # inside a function); top-level imports start at column 0.
        line = src[line_start:src.find("\n", match.end())]
        assert line.startswith((" ", "\t")), (
            f"shioaji must not be imported at module top: {line!r}"
        )


def test_template_uses_keyring_for_credentials():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "keyring" in src.lower()


def test_template_supports_env_fallback_for_credentials():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "SHIOAJI_API_KEY" in src
    assert "SHIOAJI_SECRET_KEY" in src


def test_template_supports_simulation_toggle():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    assert "SHIOAJI_SIMULATION" in src


def test_template_resolves_tmf_contract_dynamically():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    # Must enumerate Contracts.Futures and pick by code/name match.
    assert "Contracts.Futures" in src
    assert "TMF" in src or "微型台指" in src


def test_template_protocol_methods_present():
    src = TEMPLATE_ADAPTER.read_text(encoding="utf-8")
    for m in ("def connect", "def disconnect", "def submit_order",
              "def cancel_order", "def positions", "def account_equity"):
        assert m in src, f"template missing method: {m}"


def test_requirements_lists_shioaji():
    req = TEMPLATE_REQS.read_text(encoding="utf-8").lower()
    assert "shioaji" in req


def test_readme_documents_copy_path():
    readme = TEMPLATE_README.read_text(encoding="utf-8")
    assert "C:\\Trading\\live-adapters" in readme or "live-adapters" in readme
    assert "LIVE_BROKER_ADAPTER_PATH" in readme
