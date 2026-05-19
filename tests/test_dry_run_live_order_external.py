"""CLI subprocess tests for --adapter-source=external (Phase 6.B-4)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# Sample plugin: writes audit via live.audit_log so the standard
# logs/live_orders.jsonl + logs/live_fills.jsonl populate.
SAMPLE_PLUGIN = '''\
from datetime import datetime, timezone
import time


class LiveAdapter:
    name = "cli-test-fixture"
    version = "0.0.1"
    _connected = False

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def submit_order(self, order):
        from live.audit_log import write_live_order, write_live_fill
        from live.models import LiveFill

        write_live_order(order)
        ts = time.time()
        iso = datetime.now(timezone.utc).isoformat()
        fill = LiveFill(
            order_id=order.order_id, ts=ts, timestamp=iso,
            side=order.side, fill_price=order.entry, qty=order.qty,
            slippage=0.0, status="filled",
        )
        write_live_fill(fill)
        return fill

    def cancel_order(self, order_id):
        return False

    def positions(self):
        return []

    def account_equity(self):
        return 10000.0
'''


def _today_flag() -> str:
    return f"approved-{datetime.now(timezone.utc).strftime('%Y%m%d')}"


def _decision_dict(**overrides) -> dict:
    base = {
        "decision_id": "ext-test-1",
        "symbol": "XAUUSD",
        "side": "LONG",
        "entry": 23010.5,
        "stop": 22995.0,
        "target": 23040.0,
        "confidence": 0.85,
        "reason": "fibo MOB",
        "invalidation": "below stop",
        "data_sources": ["fibo_lines_filtered.json"],
        "mode": "live",
    }
    base.update(overrides)
    return base


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _base_env(*, tmp_path: Path, with_unlock: bool = True) -> dict[str, str]:
    env: dict[str, str] = {
        **os.environ,
        "LIVE_ORDERS_LOG":         str(tmp_path / "live_orders.jsonl"),
        "LIVE_FILLS_LOG":          str(tmp_path / "live_fills.jsonl"),
        "LIVE_REJECTIONS_LOG":     str(tmp_path / "live_rejections.jsonl"),
        "LIVE_ADAPTER_LOADS_LOG":  str(tmp_path / "live_adapter_loads.jsonl"),
        # Default to FAKE_LIVE_ADAPTER=true so LiveUnlockGate's adapter-
        # source precondition is satisfied. External-path tests override
        # this by setting FAKE_LIVE_ADAPTER to None and supplying
        # LIVE_BROKER_ADAPTER_PATH + LIVE_ADAPTER_ALLOWLIST.
        "FAKE_LIVE_ADAPTER":       "true",
    }
    if with_unlock:
        env.update({
            "LIVE_TRADING":         "true",
            "EXECUTION_MODE":       "live",
            "LIVE_READY_FLAG":      _today_flag(),
            "LIVE_TOKEN_HMAC":      "ext-cli-hmac",
            "ALLOWED_SYMBOLS":      "XAUUSD,EURUSD",
            "MAX_DAILY_LOSS":       "50",
            "MAX_POSITION_SIZE":    "1",
            "MIN_LIVE_CONFIDENCE":  "0.7",
            "MAX_LOSS_PER_TRADE":   "100",
            "MAX_DAILY_TRADES":     "5",
        })
    return env


def _run_cli(
    *,
    tmp_path: Path,
    decision: dict,
    adapter_source: str = "external",
    qty: float = 1.0,
    env_overrides: dict | None = None,
) -> subprocess.CompletedProcess:
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    args = [
        sys.executable, "-m", "tools.dry_run_live_order",
        "--ai-decision", str(decision_path),
        "--qty", str(qty),
        "--ai-decisions-log", str(tmp_path / "ai_decisions.jsonl"),
        "--kill-file", str(tmp_path / ".killswitch"),
        "--adapter-source", adapter_source,
        "--json",
    ]
    env = _base_env(tmp_path=tmp_path)
    if env_overrides:
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
    return subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True, env=env,
    )


# ---------- external + FAKE_LIVE_ADAPTER=true (resolver returns fake) ----

def test_external_with_fake_flag_returns_fake(tmp_path):
    """external + FAKE_LIVE_ADAPTER=true: resolver returns fake; loader
    audit row is NOT written (fake branch short-circuits)."""
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={"FAKE_LIVE_ADAPTER": "true"},
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "filled"
    # Loader was NOT invoked -> no audit row.
    assert not (tmp_path / "live_adapter_loads.jsonl").exists() \
        or (tmp_path / "live_adapter_loads.jsonl").read_text("utf-8").strip() == ""


# ---------- external + allowlisted plugin (resolver loads from path) -----

def test_external_with_allowlisted_plugin_loads_and_fills(tmp_path):
    plugin_dir = tmp_path / "live-adapters"
    plugin_dir.mkdir()
    plugin = plugin_dir / "my_broker.py"
    plugin.write_text(SAMPLE_PLUGIN, encoding="utf-8")

    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": str(plugin),
            "LIVE_ADAPTER_ALLOWLIST": str(plugin_dir),
        },
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    audit = _read_jsonl(tmp_path / "live_adapter_loads.jsonl")
    assert audit and audit[-1]["load_outcome"] == "loaded"
    assert audit[-1]["adapter_name"] == "cli-test-fixture"
    assert audit[-1]["adapter_version"] == "0.0.1"

    orders = _read_jsonl(tmp_path / "live_orders.jsonl")
    fills = _read_jsonl(tmp_path / "live_fills.jsonl")
    assert orders and orders[0]["mode"] == "live"
    assert fills and fills[0]["status"] == "filled"
    assert orders[0]["order_id"] == fills[0]["order_id"]


# ---------- external + bad path -> exit 8 -------------------------------

def test_external_with_missing_path_exit_8(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": str(tmp_path / "does_not_exist.py"),
        },
    )
    assert r.returncode == 8, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert "adapter load failed" in r.stderr.lower()
    audit = _read_jsonl(tmp_path / "live_adapter_loads.jsonl")
    assert audit and audit[-1]["load_outcome"] == "rejected_path"


def test_external_with_no_source_exit_8(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": None,
        },
    )
    assert r.returncode == 8
    assert "no_adapter_source" in r.stderr


def test_external_with_in_repo_path_exit_8(tmp_path):
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH":
                str(ROOT / "live" / "adapter_loader.py"),
        },
    )
    assert r.returncode == 8
    audit = _read_jsonl(tmp_path / "live_adapter_loads.jsonl")
    assert audit and audit[-1]["load_outcome"] == "rejected_in_tree"


def test_external_with_not_allowlisted_path_exit_8(tmp_path):
    plugin = tmp_path / "stranded.py"
    plugin.write_text(SAMPLE_PLUGIN, encoding="utf-8")
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": str(plugin),
            "LIVE_ADAPTER_ALLOWLIST": str(other_dir),
        },
    )
    assert r.returncode == 8
    audit = _read_jsonl(tmp_path / "live_adapter_loads.jsonl")
    assert audit and audit[-1]["load_outcome"] == "rejected_not_in_allowlist"


# ---------- KillSwitch pre-check fires BEFORE adapter load --------------

def test_killswitch_active_at_startup_prevents_external_adapter_load(tmp_path):
    plugin_dir = tmp_path / "live-adapters"
    plugin_dir.mkdir()
    plugin = plugin_dir / "my_broker.py"
    plugin.write_text(SAMPLE_PLUGIN, encoding="utf-8")
    (tmp_path / ".killswitch").write_text("trip", encoding="utf-8")

    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": str(plugin),
            "LIVE_ADAPTER_ALLOWLIST": str(plugin_dir),
        },
    )
    assert r.returncode == 4
    assert "kill switch active at startup" in r.stderr.lower()
    # Loader was NEVER invoked -> no audit row.
    audit_path = tmp_path / "live_adapter_loads.jsonl"
    assert not audit_path.exists() or audit_path.read_text("utf-8").strip() == ""


# ---------- pipeline gates still apply after external load --------------

def test_external_load_succeeds_but_unlock_gate_refuses(tmp_path):
    plugin_dir = tmp_path / "live-adapters"
    plugin_dir.mkdir()
    plugin = plugin_dir / "my_broker.py"
    plugin.write_text(SAMPLE_PLUGIN, encoding="utf-8")
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": str(plugin),
            "LIVE_ADAPTER_ALLOWLIST": str(plugin_dir),
            "MAX_DAILY_LOSS": None,  # break unlock condition
        },
    )
    assert r.returncode == 3
    # adapter loaded successfully (audit row says "loaded") but pipeline
    # refused at the unlock gate.
    audit = _read_jsonl(tmp_path / "live_adapter_loads.jsonl")
    assert audit and audit[-1]["load_outcome"] == "loaded"
    # And the rejection row carries layer=live_unlock_gate.
    rejs = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r_["rejection_layer"] == "live_unlock_gate" for r_ in rejs)


def test_external_load_succeeds_but_micro_gate_rejects_oversize_qty(tmp_path):
    plugin_dir = tmp_path / "live-adapters"
    plugin_dir.mkdir()
    plugin = plugin_dir / "my_broker.py"
    plugin.write_text(SAMPLE_PLUGIN, encoding="utf-8")
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        qty=99.0,
        env_overrides={
            "FAKE_LIVE_ADAPTER": None,
            "LIVE_BROKER_ADAPTER_PATH": str(plugin),
            "LIVE_ADAPTER_ALLOWLIST": str(plugin_dir),
        },
    )
    assert r.returncode == 5
    rejs = _read_jsonl(tmp_path / "live_rejections.jsonl")
    assert any(r_["rejection_layer"] == "micro_live_gate" for r_ in rejs)
    # Adapter loaded fine before the gate ran.
    audit = _read_jsonl(tmp_path / "live_adapter_loads.jsonl")
    assert audit and audit[-1]["load_outcome"] == "loaded"


# ---------- default fake mode unchanged ---------------------------------

def test_default_fake_mode_does_not_invoke_loader(tmp_path):
    """The whole point of fake-default is that loader is never touched.
    Even if LIVE_BROKER_ADAPTER_PATH is set to a bogus path in env, the
    CLI must succeed in fake mode without an audit row."""
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        adapter_source="fake",
        env_overrides={
            "LIVE_BROKER_ADAPTER_PATH": "C:/should/be/ignored.py",
        },
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    body = json.loads(r.stdout.strip().splitlines()[-1])
    assert body["outcome"] == "filled"
    # Audit log must not exist (loader untouched in fake mode).
    audit = tmp_path / "live_adapter_loads.jsonl"
    assert not audit.exists() or audit.read_text("utf-8").strip() == ""


def test_fake_default_explicit_flag(tmp_path):
    """--adapter-source=fake is the explicit form of the default."""
    r = _run_cli(
        tmp_path=tmp_path,
        decision=_decision_dict(),
        adapter_source="fake",
    )
    assert r.returncode == 0, r.stderr


# ---------- argparse rejects unknown adapter source --------------------

def test_invalid_adapter_source_rejected(tmp_path):
    decision_path = tmp_path / "d.json"
    decision_path.write_text(json.dumps(_decision_dict()), encoding="utf-8")
    args = [
        sys.executable, "-m", "tools.dry_run_live_order",
        "--ai-decision", str(decision_path),
        "--adapter-source", "garbage",
    ]
    r = subprocess.run(
        args, cwd=str(ROOT), capture_output=True, text=True,
        env=_base_env(tmp_path=tmp_path, with_unlock=False),
    )
    assert r.returncode == 2  # argparse exit
    assert "garbage" in r.stderr.lower() or "invalid" in r.stderr.lower()
