"""Safety tests for the Hermes integration scaffold.

Hermes itself is NOT installed by this repo. These tests only inspect
the scaffold files (scripts\\start_hermes.ps1, configs\\hermes_allowlist.yaml,
configs\\hermes.env.example) to confirm:

  * mock-mode env vars are pinned and cannot be flipped to live;
  * no broker SDK names appear anywhere in the Hermes-related files;
  * no broker credentials / CTPro tokens are referenced;
  * the path allowlist contains exactly the four approved roots;
  * the launcher refuses to execute anything until HERMES_EXEC is set.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HERMES_FILES = {
    "start": ROOT / "scripts" / "start_hermes.ps1",
    "allowlist": ROOT / "configs" / "hermes_allowlist.yaml",
    "env": ROOT / "configs" / "hermes.env.example",
}

BROKER_SDKS = [
    "shioaji", "ib_insync", "ibapi", "MetaTrader5",
    "ccxt", "binance", "alpaca", "oandapyV20",
]

BROKER_CREDS = [
    "CTPro", "ctpro", "CTPRO",
    "SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY",
    "IB_USERNAME", "IB_PASSWORD",
    "MT5_LOGIN", "MT5_PASSWORD",
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
]


# ---------------------------------------------------------------- presence

def test_all_scaffold_files_exist():
    for name, path in HERMES_FILES.items():
        assert path.exists(), f"missing Hermes scaffold file: {name} -> {path}"


# ---------------------------------------------------------------- mock envelope

def test_start_script_pins_mock_env_vars():
    txt = HERMES_FILES["start"].read_text(encoding="utf-8")
    required = {
        "LIVE_TRADING": "false",
        "EXECUTION_MODE": "mock",
        "BROKER_MODE": "mock",
        "CUDA_VISIBLE_DEVICES": "1",
        "PROJECT_ROOT": r"C:\\Trading\\ai_fibo_vision_trader",
        "DATA_ROOT": r"D:\\TradingData",
    }
    for key, val in required.items():
        pattern = rf'\$env:{key}\s*=\s*"{val}"'
        assert re.search(pattern, txt), f"start_hermes.ps1 must set {key}={val} (pattern not found)"


def test_no_file_enables_live_mode():
    forbidden_patterns = [
        r'LIVE_TRADING\s*=\s*"?true"?',
        r'EXECUTION_MODE\s*=\s*"?live"?',
        r'BROKER_MODE\s*=\s*"?live"?',
    ]
    for path in HERMES_FILES.values():
        txt = path.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            for m in re.finditer(pat, txt, re.IGNORECASE):
                # Allow occurrences inside a refusal/skip diagnostic line.
                line = txt[: m.start()].splitlines()[-1] if "\n" in txt[: m.start()] else txt[: m.start()]
                full_line = txt.splitlines()[txt[: m.start()].count("\n")]
                lowered = full_line.lower()
                if any(kw in lowered for kw in ("refuse", "refusing", "ignored", "skip", "must be", "$pair")):
                    continue
                pytest.fail(f"{path.name}: forbidden pattern '{pat}' on line: {full_line!r}")


def test_start_script_refuses_to_launch_without_verified_entrypoint():
    """The launcher now launches Hermes via the venv Python entrypoint
    (`from hermes_cli.main import main`) instead of executing hermes.exe
    directly, because Windows AppLocker / application-control policies
    can silently block the .exe. The safety intent is unchanged: refuse
    to launch unless a verified entrypoint exists. Mechanism swapped
    from HERMES_EXEC -> HERMES_PY (the venv python path)."""
    txt = HERMES_FILES["start"].read_text(encoding="utf-8")
    # New mechanism: $env:HERMES_PY must point at a real venv python.
    assert "$env:HERMES_PY" in txt
    assert "Hermes venv python not found" in txt
    # HERMES_EXEC, if set, is deliberately ignored to avoid Windows policy block.
    assert "HERMES_EXEC is set but ignored" in txt
    # Example env file must still leave HERMES_EXEC blank by default
    # (it has no effect now, but a populated value would mislead operators).
    env_txt = HERMES_FILES["env"].read_text(encoding="utf-8")
    assert re.search(r"^\s*HERMES_EXEC\s*=\s*$", env_txt, re.MULTILINE), (
        "configs/hermes.env.example must leave HERMES_EXEC blank by default"
    )


def test_start_script_blocks_forbidden_credential_envs():
    txt = HERMES_FILES["start"].read_text(encoding="utf-8")
    for cred in ("SHIOAJI_API_KEY", "IB_PASSWORD", "MT5_LOGIN", "BINANCE_API_KEY", "ALPACA_API_KEY", "CTPRO_USER"):
        assert cred in txt, f"start_hermes.ps1 must list {cred} in its forbidden-env block"


# ---------------------------------------------------------------- broker SDK

def test_no_broker_sdk_referenced_anywhere():
    """Broker SDK names may appear ONLY inside denial sections.

    For YAML: parse it and assert broker names never appear in
    ``allowed_paths`` or ``allowed_commands`` values.
    For everything else (the .ps1 launcher, the .env example): scan
    each line and allow a hit only if the surrounding code block is
    clearly a denial/refusal context (the launcher's ``$Forbidden``
    array, for example).
    """
    yaml = pytest.importorskip("yaml")

    # --- YAML: structural check ---
    cfg = yaml.safe_load(HERMES_FILES["allowlist"].read_text(encoding="utf-8")) or {}
    safe_keys = {"allowed_paths", "allowed_commands"}
    for key in safe_keys:
        for value in cfg.get(key) or []:
            for sdk in BROKER_SDKS:
                assert sdk.lower() not in str(value).lower(), (
                    f"allowlist.{key} contains broker SDK '{sdk}': {value!r}"
                )

    # --- non-YAML files: line-based check with explicit denial whitelist ---
    denial_markers = (
        "denied", "deny", "refuse", "refusing", "forbidden", "$forbidden",
        "skip", "ignored", "must not", "never",
    )
    for name, path in HERMES_FILES.items():
        if name == "allowlist":
            continue  # handled structurally above
        txt = path.read_text(encoding="utf-8")
        for sdk in BROKER_SDKS:
            for m in re.finditer(rf"\b{sdk}\b", txt, re.IGNORECASE):
                line_idx = txt[: m.start()].count("\n")
                full_line = txt.splitlines()[line_idx].lower()
                if any(kw in full_line for kw in denial_markers):
                    continue
                pytest.fail(f"{path.name}: broker SDK '{sdk}' outside denial context: {full_line!r}")


def test_no_broker_credentials_in_env_or_allowlist_values():
    # Credentials may be NAMED in denial lists; they must never appear as values.
    for path in (HERMES_FILES["env"],):
        txt = path.read_text(encoding="utf-8")
        for cred in BROKER_CREDS:
            for m in re.finditer(rf"^\s*{cred}\s*=\s*(\S+)", txt, re.MULTILINE):
                pytest.fail(f"{path.name}: assigns credential {cred}={m.group(1)!r}")


# ---------------------------------------------------------------- allowlist

def test_allowlist_contains_exactly_the_four_roots():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load(HERMES_FILES["allowlist"].read_text(encoding="utf-8")) or {}
    paths = set(cfg.get("allowed_paths") or [])
    expected = {
        r"C:\Trading\ai_fibo_vision_trader",
        r"C:\Trading\configs",
        r"C:\Trading\logs",
        r"D:\TradingData",
    }
    assert paths == expected, f"allowlist mismatch: got {paths}, expected {expected}"


def test_allowlist_denies_credentials_and_brokers():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load(HERMES_FILES["allowlist"].read_text(encoding="utf-8")) or {}
    denied = set(cfg.get("denied_globs") or [])
    must_deny = {"**/.env", "**/credentials*", "**/secrets*", "**/CTPro/**", "**/shioaji*", "**/ib_insync*", "**/MetaTrader5*"}
    missing = must_deny - denied
    assert not missing, f"allowlist denied_globs missing: {missing}"


def test_allowlist_denied_commands_cover_broker_sdks():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load(HERMES_FILES["allowlist"].read_text(encoding="utf-8")) or {}
    denied_cmds = " ".join(cfg.get("denied_commands") or [])
    for token in BROKER_SDKS + ["live_order", "place_order", "real_order", "CTPro"]:
        assert token in denied_cmds, f"denied_commands must mention {token}"


def test_allowlist_does_not_include_unapproved_drives():
    yaml = pytest.importorskip("yaml")
    cfg = yaml.safe_load(HERMES_FILES["allowlist"].read_text(encoding="utf-8")) or {}
    for p in cfg.get("allowed_paths") or []:
        drive = p[:2].upper()
        assert drive in {"C:", "D:"}, f"allowed_paths contains unapproved drive: {p}"
        assert "broker" not in p.lower()
        assert "ctpro" not in p.lower()


# ---------------------------------------------------------------- tool calling rule

def test_system_prompt_forbids_fake_tool_call_json():
    """The project system prompt must include the rule that Hermes invokes
    real tools rather than printing a tool-call JSON envelope as text.

    Added after the 2026-05-19 regression where the user-scope Hermes
    `model.default` was downgraded to `qwen2.5-coder:7b-64k` and the model
    started emitting `{"name":"terminal","arguments":{...}}` as plain text
    instead of using the native tool-call channel. The prompt-side defence
    is to instruct the model explicitly. See:
      * prompts/hermes_project_system_prompt.md  Tool calling
      * docs/hermes_operating_runbook.md  7.5
      * scripts/hermes_tool_call_smoke.ps1
    """
    prompt = ROOT / "prompts" / "hermes_project_system_prompt.md"
    assert prompt.exists(), f"missing system prompt: {prompt}"
    txt = prompt.read_text(encoding="utf-8")

    # Section heading must exist.
    assert "Tool calling" in txt, "system prompt missing a 'Tool calling' section"

    # The JSON envelope shape must be shown explicitly so the model knows
    # exactly what NOT to emit.
    assert '"name"' in txt and '"arguments"' in txt, (
        "system prompt must spell out the JSON tool-envelope shape that is forbidden"
    )

    # And the fallback rule -- if no tool is available, plain prose, never
    # fabricate -- must be present too.
    lowered = txt.lower()
    assert "never fabricate" in lowered or "never print" in lowered or "do not print" in lowered or "do not emit" in lowered, (
        "system prompt must explicitly forbid printing/fabricating tool JSON as text"
    )
