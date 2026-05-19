"""Unit tests for live.adapter_loader (Phase 6.B-3a)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"


from live.adapter_loader import (  # noqa: E402
    LiveAdapterLoadError,
    REPO_ROOT,
    _get_allowlist,
    load_live_adapter,
    resolve_live_adapter,
)
from live.broker_adapter_protocol import LiveBrokerAdapterProtocol  # noqa: E402
from live.fake_live_adapter import FakeLiveBrokerAdapter  # noqa: E402


# ----------------------------------------------------------------- fixtures


SAMPLE_PLUGIN = '''\
"""Test plugin: stand-alone, no repo imports, satisfies Protocol via
duck typing. Carries a sentinel string the audit-log test asserts is
NOT written into logs/live_adapter_loads.jsonl."""

PLUGIN_SENTINEL = "PLUGIN_BODY_SENTINEL_QXZ_42"


class _Fill:
    def __init__(self, order):
        self.order_id = order.order_id
        self.ts = 1.0
        self.timestamp = "fixture"
        self.side = order.side
        self.fill_price = order.entry
        self.qty = order.qty
        self.slippage = 0.0
        self.status = "filled"
        self.mode = "live"


class LiveAdapter:
    name = "test-fixture"
    version = "0.0.1"
    _connected = False

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def submit_order(self, order):
        return _Fill(order)

    def cancel_order(self, order_id):
        return False

    def positions(self):
        return []

    def account_equity(self):
        return 10000.0
'''

PLUGIN_PROTOCOL_VIOLATION = '''\
"""Test plugin: class named LiveAdapter but missing methods."""

class LiveAdapter:
    name = "broken"

    def connect(self):
        pass
    # Intentionally missing: submit_order, cancel_order, positions,
    # account_equity, disconnect.
'''

PLUGIN_NO_LIVEADAPTER_CLASS = '''\
"""Test plugin with a satisfying class but wrong name."""

class _Fill:
    pass

class SomeOtherAdapter:
    name = "wrongly-named"

    def connect(self): pass
    def disconnect(self): pass
    def submit_order(self, o): return _Fill()
    def cancel_order(self, oid): return False
    def positions(self): return []
    def account_equity(self): return 0.0
'''

PLUGIN_SYNTAX_ERROR = '''\
"""Test plugin with a syntax error."""

class LiveAdapter
    name = "bad"
'''


def _write_plugin(dir_path: Path, body: str, name: str = "plugin.py") -> Path:
    p = dir_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _read_audit(tmp_path: Path) -> list[dict]:
    log = tmp_path / "live_adapter_loads.jsonl"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _setup_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LIVE_ADAPTER_LOADS_LOG", str(tmp_path / "live_adapter_loads.jsonl"))
    monkeypatch.setenv("LIVE_ADAPTER_ALLOWLIST", str(tmp_path))


# ----------------------------------------------------------------- resolver


def test_fake_wins_over_path(monkeypatch, tmp_path):
    """FAKE_LIVE_ADAPTER=true must short-circuit even when
    LIVE_BROKER_ADAPTER_PATH is set to a bogus path."""
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")
    monkeypatch.setenv("LIVE_BROKER_ADAPTER_PATH", "C:/does/not/exist.py")
    adapter = resolve_live_adapter()
    assert isinstance(adapter, FakeLiveBrokerAdapter)


def test_fake_path_does_not_write_audit_log(monkeypatch, tmp_path):
    """When fake is chosen, the loader is not invoked; no audit row."""
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_LIVE_ADAPTER", "true")
    monkeypatch.setenv("LIVE_BROKER_ADAPTER_PATH", "C:/does/not/exist.py")
    resolve_live_adapter()
    assert _read_audit(tmp_path) == []


def test_no_source_raises(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("FAKE_LIVE_ADAPTER", raising=False)
    monkeypatch.delenv("LIVE_BROKER_ADAPTER_PATH", raising=False)
    with pytest.raises(LiveAdapterLoadError) as e:
        resolve_live_adapter()
    assert "no_adapter_source" in str(e.value)


# ----------------------------------------------------------------- path gate


def test_empty_path_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter("")
    assert "rejected_path:empty_path" in str(e.value)


def test_missing_path_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(tmp_path / "nope.py"))
    assert "rejected_path:does_not_exist" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_path"


def test_directory_path_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(tmp_path))
    assert "not_a_file" in str(e.value)


def test_non_python_extension_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = tmp_path / "plugin.txt"
    p.write_text("not python", encoding="utf-8")
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(p))
    assert "rejected_not_python" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_not_python"


def test_in_repo_path_rejected(monkeypatch, tmp_path):
    """Even with an allowlist that covers the repo, an in-tree path
    must be rejected by the in-tree gate."""
    _setup_paths(monkeypatch, tmp_path)
    # Adjust allowlist to include both tmp and (pretend) repo root,
    # but the in-tree check should still reject the repo file.
    monkeypatch.setenv(
        "LIVE_ADAPTER_ALLOWLIST", f"{tmp_path},{REPO_ROOT}"
    )
    repo_py = REPO_ROOT / "live" / "adapter_loader.py"
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(repo_py))
    assert "rejected_in_tree" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_in_tree"


def test_not_in_allowlist_rejected(monkeypatch, tmp_path):
    """File exists outside repo but is not in any allowlisted root."""
    _setup_paths(monkeypatch, tmp_path)
    # Move plugin to a subdirectory NOT in the allowlist.
    other_dir = tmp_path.parent / "not_allowlisted_dir"
    other_dir.mkdir(exist_ok=True)
    p = other_dir / "plugin.py"
    p.write_text(SAMPLE_PLUGIN, encoding="utf-8")
    # Allowlist is tmp_path only (set by _setup_paths).
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(p))
    assert "rejected_not_in_allowlist" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_not_in_allowlist"
    assert rows[-1]["allowlist_check"] == "failed"


# ----------------------------------------------------------------- module gate


def test_valid_plugin_loads_and_satisfies_protocol(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, SAMPLE_PLUGIN)
    adapter = load_live_adapter(str(p))
    assert isinstance(adapter, LiveBrokerAdapterProtocol)
    assert adapter.name == "test-fixture"
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "loaded"
    assert rows[-1]["protocol_check"] == "passed"
    assert rows[-1]["adapter_name"] == "test-fixture"
    assert rows[-1]["adapter_version"] == "0.0.1"


def test_plugin_missing_liveadapter_class_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, PLUGIN_NO_LIVEADAPTER_CLASS)
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(p))
    assert "rejected_no_class" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_no_class"


def test_plugin_protocol_violation_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, PLUGIN_PROTOCOL_VIOLATION)
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(p))
    assert "rejected_protocol" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_protocol"
    assert rows[-1]["protocol_check"] == "failed"


def test_plugin_syntax_error_rejected(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, PLUGIN_SYNTAX_ERROR, name="syntax.py")
    with pytest.raises(LiveAdapterLoadError) as e:
        load_live_adapter(str(p))
    assert "rejected_syntax" in str(e.value)
    rows = _read_audit(tmp_path)
    assert rows[-1]["load_outcome"] == "rejected_syntax"


# ----------------------------------------------------------------- audit log


def test_audit_success_row_contents(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, SAMPLE_PLUGIN)
    load_live_adapter(str(p))
    rows = _read_audit(tmp_path)
    row = rows[-1]
    assert row["event_type"] == "adapter_load"
    assert row["load_outcome"] == "loaded"
    assert row["path"] == str(p.resolve())
    assert row["path_sha256"]
    assert len(row["path_sha256"]) == 64
    assert row["allowlist_check"] == "passed"
    assert row["protocol_check"] == "passed"
    assert row["adapter_name"] == "test-fixture"
    assert row["adapter_version"] == "0.0.1"
    assert row["mode"] == "live"
    assert row["error_message"] is None


def test_audit_rejection_row_has_error_message(monkeypatch, tmp_path):
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, PLUGIN_SYNTAX_ERROR, name="syn.py")
    with pytest.raises(LiveAdapterLoadError):
        load_live_adapter(str(p))
    rows = _read_audit(tmp_path)
    row = rows[-1]
    assert row["load_outcome"] == "rejected_syntax"
    assert "SyntaxError" in (row["error_message"] or "")
    assert row["mode"] == "live"


def test_audit_does_not_contain_plugin_module_body(monkeypatch, tmp_path):
    """The audit log carries path + sha256 + outcome -- never the
    plugin's source text or any sentinel from the plugin body."""
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, SAMPLE_PLUGIN)
    load_live_adapter(str(p))
    audit_text = (tmp_path / "live_adapter_loads.jsonl").read_text(encoding="utf-8")
    assert "PLUGIN_BODY_SENTINEL_QXZ_42" not in audit_text


def test_audit_does_not_contain_credentials(monkeypatch, tmp_path):
    """Loader must never read or write any broker credential env. We
    set obviously-tagged credentials in env and check the audit row
    does not echo any of them."""
    _setup_paths(monkeypatch, tmp_path)
    sentinels = {
        "SHIOAJI_API_KEY": "FAKE-SHIOAJI-KEY-SENTINEL",
        "IB_PASSWORD":     "FAKE-IB-PASSWORD-SENTINEL",
        "CTPRO_TOKEN":     "FAKE-CTPRO-TOKEN-SENTINEL",
        "BINANCE_API_KEY": "FAKE-BINANCE-KEY-SENTINEL",
    }
    for k, v in sentinels.items():
        monkeypatch.setenv(k, v)
    p = _write_plugin(tmp_path, SAMPLE_PLUGIN)
    load_live_adapter(str(p))
    audit_text = (tmp_path / "live_adapter_loads.jsonl").read_text(encoding="utf-8")
    for k, v in sentinels.items():
        assert v not in audit_text, f"credential {k} leaked into audit log"


def test_path_sha256_is_consistent(monkeypatch, tmp_path):
    """Loading the same file twice writes the same sha256."""
    _setup_paths(monkeypatch, tmp_path)
    p = _write_plugin(tmp_path, SAMPLE_PLUGIN)
    load_live_adapter(str(p))
    load_live_adapter(str(p))
    rows = _read_audit(tmp_path)
    assert len(rows) == 2
    assert rows[0]["path_sha256"] == rows[1]["path_sha256"]


# ----------------------------------------------------------------- allowlist


def test_allowlist_filters_out_repo_root(monkeypatch):
    """Even if operator sets the repo root in the allowlist env,
    _get_allowlist must drop it."""
    monkeypatch.setenv("LIVE_ADAPTER_ALLOWLIST", str(REPO_ROOT))
    allowlist = _get_allowlist()
    assert REPO_ROOT not in allowlist


def test_allowlist_filters_subdirs_of_repo(monkeypatch):
    monkeypatch.setenv("LIVE_ADAPTER_ALLOWLIST", str(REPO_ROOT / "tests"))
    allowlist = _get_allowlist()
    assert allowlist == []


def test_allowlist_default_when_env_empty(monkeypatch):
    monkeypatch.delenv("LIVE_ADAPTER_ALLOWLIST", raising=False)
    allowlist = _get_allowlist()
    # default is C:\Trading\live-adapters; not in repo so it survives
    assert allowlist
    for p in allowlist:
        assert not _is_path_under_repo(p)


def _is_path_under_repo(p: Path) -> bool:
    try:
        p.relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False
