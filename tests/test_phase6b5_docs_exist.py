"""Phase 6.B-5 doc-existence + content-keyword + structural guards.

Phase 6.B-5 is a docs-only phase: 4 operator-facing markdown files
plus this test. No production source is modified. The test verifies:

  1. All 4 documents exist and are non-trivial.
  2. Each document contains the keywords that anchor its purpose
     (cheap regression check against accidental truncation).
  3. The main repo still does NOT import ``keyring`` anywhere -- the
     credential-store package belongs to the operator's out-of-tree
     adapter venv, never to the main repo.
  4. ``requirements.txt`` does not include ``keyring``.

These are structural / regression guards. The actual quality of the
docs is human-reviewed by the operator before any real trade.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"


PHASE6B5_DOCS = [
    ROOT / "docs" / "live_adapter_runbook.md",
    ROOT / "docs" / "micro_live_readiness_checklist.md",
    ROOT / "docs" / "emergency_stop.md",
    ROOT / "docs" / "first_real_trade_procedure.md",
]


# ---------- existence + non-trivial -------------------------------------

@pytest.mark.parametrize("doc_path", PHASE6B5_DOCS)
def test_doc_exists(doc_path: Path):
    assert doc_path.exists(), f"missing: {doc_path.relative_to(ROOT)}"


@pytest.mark.parametrize("doc_path", PHASE6B5_DOCS)
def test_doc_is_non_trivial(doc_path: Path):
    """Each doc must be at least 500 chars -- a hard regression guard
    against an accidental wipe or empty stub."""
    text = doc_path.read_text(encoding="utf-8")
    assert len(text) >= 500, (
        f"{doc_path.relative_to(ROOT)} is too short "
        f"({len(text)} chars; require >= 500)"
    )


# ---------- per-doc keyword anchors -------------------------------------

def test_runbook_mentions_keyring():
    text = (ROOT / "docs" / "live_adapter_runbook.md").read_text(encoding="utf-8")
    assert "keyring" in text.lower(), (
        "live_adapter_runbook.md must reference 'keyring' "
        "(the credential-store package)"
    )


def test_runbook_mentions_protocol():
    text = (ROOT / "docs" / "live_adapter_runbook.md").read_text(encoding="utf-8")
    assert "LiveBrokerAdapterProtocol" in text, (
        "live_adapter_runbook.md must reference the Protocol name"
    )


def test_emergency_stop_mentions_killswitch():
    text = (ROOT / "docs" / "emergency_stop.md").read_text(encoding="utf-8")
    lower = text.lower()
    assert "killswitch" in lower or "kill_switch" in lower or "kill switch" in lower, (
        "emergency_stop.md must reference the kill switch concept"
    )


def test_emergency_stop_mentions_three_levels():
    text = (ROOT / "docs" / "emergency_stop.md").read_text(encoding="utf-8")
    assert "Level 1" in text and "Level 2" in text and "Level 3" in text, (
        "emergency_stop.md must define three escalation levels"
    )


def test_readiness_checklist_mentions_max_daily_loss():
    text = (
        ROOT / "docs" / "micro_live_readiness_checklist.md"
    ).read_text(encoding="utf-8")
    assert "MAX_DAILY_LOSS" in text, (
        "readiness checklist must reference MAX_DAILY_LOSS env"
    )


def test_readiness_checklist_has_signoff():
    text = (
        ROOT / "docs" / "micro_live_readiness_checklist.md"
    ).read_text(encoding="utf-8")
    assert "Sign-off" in text or "Signature" in text, (
        "readiness checklist must have an explicit sign-off section"
    )


def test_first_trade_procedure_mentions_live_ready_flag():
    text = (
        ROOT / "docs" / "first_real_trade_procedure.md"
    ).read_text(encoding="utf-8")
    assert "LIVE_READY_FLAG" in text, (
        "first-trade procedure must reference LIVE_READY_FLAG"
    )


def test_first_trade_procedure_has_t_minus_t_zero_t_plus():
    """Phase structure: T-1 / T-0 / T+post."""
    text = (
        ROOT / "docs" / "first_real_trade_procedure.md"
    ).read_text(encoding="utf-8")
    assert "T-1" in text and "T-0" in text and "T+post" in text, (
        "first-trade procedure must follow T-1 / T-0 / T+post phases"
    )


# ---------- repo-wide: no keyring in main source ------------------------

def test_main_repo_does_not_import_keyring():
    """``keyring`` is the credential store used by the operator's
    out-of-tree adapter; it MUST NOT appear as an import in the main
    repo's source tree. The docs may discuss keyring; only Python
    source is scanned."""
    skip_dirs = {
        ".git", ".venv", "venv", "node_modules",
        "__pycache__", ".pytest_cache",
        "tests",   # tests may grep for the string; not import
        "docs",    # docs may discuss the package
    }
    pat = re.compile(r"^\s*(?:import\s+keyring|from\s+keyring\b)", re.MULTILINE)
    offenders: list[str] = []
    for p in ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if pat.search(text):
            offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, (
        "main repo imports keyring (must be operator-side only): "
        f"{offenders}"
    )


def test_keyring_not_in_requirements_txt():
    req_path = ROOT / "requirements.txt"
    if not req_path.exists():
        return  # nothing to check
    content = req_path.read_text(encoding="utf-8").lower()
    # Match a real dep line: starts with "keyring" possibly followed
    # by a version specifier. Comments are allowed to mention it.
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if re.match(r"^keyring(\s*[<>=!~].*)?$", stripped):
            pytest.fail(
                f"requirements.txt declares keyring as a dependency: "
                f"{line!r}. keyring belongs to the operator's adapter venv, "
                f"not the main repo."
            )


# ---------- docs do not leak the prohibited execution-bypass switches ---

def test_docs_do_not_set_live_trading_truthy_as_an_instruction():
    """The docs DO discuss the LIVE_TRADING env name and may show that
    it should be set to ``true`` -- that's instructional. They must
    NOT, however, contain a Python or YAML literal that LOOKS like an
    in-source enablement, e.g. a code block that an automated
    extraction tool might lift into a config file by accident. We
    settle for a soft check: explicit prose phrasings are allowed,
    but a quoted assignment in shell-comment form is flagged.

    Practically: this guard ensures the docs do not later morph into
    a config snippet that gets accidentally executed."""
    # We deliberately allow `$env:LIVE_TRADING = "true"` in PowerShell
    # snippets (these are operator instructions, not config). What
    # we disallow is a stand-alone .env-file line like
    # `LIVE_TRADING=true` on its own line, which an extraction tool
    # might pick up.
    pat = re.compile(r"^LIVE_TRADING\s*=\s*['\"]?(?:1|true|yes|on)\b", re.IGNORECASE | re.MULTILINE)
    offenders: list[tuple[str, str]] = []
    for d in PHASE6B5_DOCS:
        text = d.read_text(encoding="utf-8")
        for m in pat.finditer(text):
            offenders.append((str(d.relative_to(ROOT)), m.group(0)))
    assert not offenders, (
        f"docs contain a bare LIVE_TRADING=truthy line (could be "
        f"mis-extracted as config): {offenders}"
    )
