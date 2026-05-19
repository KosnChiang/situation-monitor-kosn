"""LiveBrokerAdapter loader skeleton (Phase 6.B-3a).

Decides which live adapter the (future) live runtime should use:

  1. FAKE_LIVE_ADAPTER=true   -> FakeLiveBrokerAdapter (and the loader
                                  does NOT read LIVE_BROKER_ADAPTER_PATH)
  2. LIVE_BROKER_ADAPTER_PATH -> validate path + import module +
                                  instantiate + Protocol check
  3. neither set              -> LiveAdapterLoadError

Strictly mock-only at the loader layer:
  * No broker SDK import.
  * No outbound HTTP library.
  * No broker credential env read.
  * Audit log NEVER contains the plugin's module source / body /
    credentials -- only path, sha256, and the gate outcome.

This file is the loader skeleton ONLY. It is NOT wired into the
pipeline, NOT wired into the CLI, NOT exercised by any production
code path in this commit. Phase 6.B-4 will integrate.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from live.broker_adapter_protocol import LiveBrokerAdapterProtocol
from live.fake_live_adapter import FakeLiveBrokerAdapter


REPO_ROOT = Path(__file__).resolve().parent.parent

TRUTHY = {"1", "true", "yes", "on"}


class LiveAdapterLoadError(RuntimeError):
    """Raised when an adapter source cannot be resolved or a path-based
    adapter fails any of the load gates."""


# ---------- env / config -------------------------------------------------


def _default_audit_log_path() -> Path:
    return Path(os.getenv("LIVE_ADAPTER_LOADS_LOG", "logs/live_adapter_loads.jsonl"))


def _get_allowlist() -> list[Path]:
    """Return the list of allowlisted directory roots a path-based
    adapter is permitted to live under.

    Default: ``C:\\Trading\\live-adapters`` (Windows-only project).
    Env override: ``LIVE_ADAPTER_ALLOWLIST`` (comma-separated).

    Self-protection: any allowlist entry that resolves to a path inside
    (or equal to) the repo root is dropped. This ensures the loader can
    never be tricked into loading an adapter from the main repo even if
    the operator mis-configures the env.
    """
    raw = (os.getenv("LIVE_ADAPTER_ALLOWLIST", "") or "").strip()
    if raw:
        candidates = [Path(p.strip()).resolve() for p in raw.split(",") if p.strip()]
    else:
        candidates = [Path(r"C:\Trading\live-adapters").resolve()]

    filtered: list[Path] = []
    for c in candidates:
        try:
            c.relative_to(REPO_ROOT)
            continue
        except ValueError:
            filtered.append(c)
    return filtered


# ---------- audit log ----------------------------------------------------


def _write_adapter_load_audit(
    *,
    path: Optional[Path],
    path_sha256: Optional[str],
    allowlist_check: str,
    protocol_check: str,
    adapter_name: Optional[str],
    adapter_version: Optional[str],
    load_outcome: str,
    error_message: Optional[str],
) -> None:
    log_path = _default_audit_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "event_type": "adapter_load",
        "ts": time.time(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": str(path) if path is not None else None,
        "path_sha256": path_sha256,
        "allowlist_check": allowlist_check,
        "protocol_check": protocol_check,
        "adapter_name": adapter_name,
        "adapter_version": adapter_version,
        "load_outcome": load_outcome,
        "error_message": error_message,
        "mode": "live",
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ---------- path validation ---------------------------------------------


def _is_under(p: Path, root: Path) -> bool:
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _path_sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def _validate_path(path_str: str) -> Path:
    """Run every path-level gate. Raises ``LiveAdapterLoadError`` with
    a structured message prefixed by the load_outcome on first failure.
    Returns the resolved Path on success."""
    if not path_str or not path_str.strip():
        raise LiveAdapterLoadError("rejected_path:empty_path")

    p = Path(path_str).resolve()
    if not p.exists():
        raise LiveAdapterLoadError(f"rejected_path:does_not_exist:{p}")
    if not p.is_file():
        raise LiveAdapterLoadError(f"rejected_path:not_a_file:{p}")
    if p.suffix.lower() != ".py":
        raise LiveAdapterLoadError(f"rejected_not_python:{p.suffix}")

    try:
        p.relative_to(REPO_ROOT)
        raise LiveAdapterLoadError(
            f"rejected_in_tree:{p}_inside_{REPO_ROOT}"
        )
    except ValueError:
        pass

    allowlist = _get_allowlist()
    if not any(_is_under(p, root) for root in allowlist):
        raise LiveAdapterLoadError(
            f"rejected_not_in_allowlist:{p}_allowlist="
            f"{[str(r) for r in allowlist]}"
        )

    return p


# ---------- module / class discovery ------------------------------------


def _find_adapter_class(module: Any) -> Optional[type]:
    """Convention-only. The plugin module must expose a class named
    exactly ``LiveAdapter``. No fallback class scanning -- a wrong
    convention is operator error, not a fuzzy-matching opportunity."""
    cls = getattr(module, "LiveAdapter", None)
    if cls is None or not inspect.isclass(cls):
        return None
    return cls


# ---------- top-level entry points --------------------------------------


def load_live_adapter(path: str | Path) -> LiveBrokerAdapterProtocol:
    """Validate + import + instantiate. Writes one audit row per call,
    on every outcome (success or failure)."""
    path_str = str(path)

    base_audit = dict(
        path=Path(path_str) if path_str else None,
        path_sha256=None,
        allowlist_check="not_run",
        protocol_check="not_run",
        adapter_name=None,
        adapter_version=None,
    )

    # ---- Stage 1-3: path gates ----
    try:
        p = _validate_path(path_str)
    except LiveAdapterLoadError as e:
        msg = str(e)
        outcome = msg.split(":", 1)[0]
        allowlist_check = "failed" if outcome == "rejected_not_in_allowlist" else "not_run"
        _write_adapter_load_audit(
            **{**base_audit, "allowlist_check": allowlist_check},
            load_outcome=outcome,
            error_message=msg,
        )
        raise

    base_audit["path"] = p
    base_audit["allowlist_check"] = "passed"
    base_audit["path_sha256"] = _path_sha256(p)

    # ---- Stage 4: import ----
    try:
        spec = importlib.util.spec_from_file_location(
            "live_adapter_plugin_" + base_audit["path_sha256"][:8], p
        )
        if spec is None or spec.loader is None:
            raise LiveAdapterLoadError("rejected_syntax:cannot_make_spec")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except LiveAdapterLoadError:
        _write_adapter_load_audit(
            **base_audit,
            load_outcome="rejected_syntax",
            error_message="cannot_make_spec",
        )
        raise
    except SyntaxError as e:
        msg = f"SyntaxError:line_{e.lineno}:{e.msg}"
        _write_adapter_load_audit(
            **base_audit,
            load_outcome="rejected_syntax",
            error_message=msg,
        )
        raise LiveAdapterLoadError(f"rejected_syntax:{msg}") from e
    except Exception as e:
        msg = f"{type(e).__name__}:{e}"
        _write_adapter_load_audit(
            **base_audit,
            load_outcome="rejected_syntax",
            error_message=msg,
        )
        raise LiveAdapterLoadError(f"rejected_syntax:{msg}") from e

    # ---- Stage 5: class discovery ----
    cls = _find_adapter_class(module)
    if cls is None:
        _write_adapter_load_audit(
            **{**base_audit, "protocol_check": "failed"},
            load_outcome="rejected_no_class",
            error_message="no_LiveAdapter_class_in_module",
        )
        raise LiveAdapterLoadError(
            f"rejected_no_class:{p}_has_no_LiveAdapter_class"
        )

    # ---- Stage 6: instantiate + Protocol check ----
    try:
        instance = cls()
    except Exception as e:
        msg = f"{type(e).__name__}:{e}"
        cls_name_val = getattr(cls, "name", None)
        _write_adapter_load_audit(
            **{
                **base_audit,
                "protocol_check": "failed",
                "adapter_name": cls_name_val if isinstance(cls_name_val, str) else None,
            },
            load_outcome="rejected_protocol",
            error_message=f"instantiation_failed:{msg}",
        )
        raise LiveAdapterLoadError(
            f"rejected_protocol:instantiation_failed:{msg}"
        ) from e

    if not isinstance(instance, LiveBrokerAdapterProtocol):
        inst_name_val = getattr(instance, "name", None)
        _write_adapter_load_audit(
            **{
                **base_audit,
                "protocol_check": "failed",
                "adapter_name": inst_name_val if isinstance(inst_name_val, str) else None,
            },
            load_outcome="rejected_protocol",
            error_message="instance_does_not_satisfy_LiveBrokerAdapterProtocol",
        )
        raise LiveAdapterLoadError(
            f"rejected_protocol:{cls.__name__}_does_not_satisfy_Protocol"
        )

    # ---- Success ----
    name = getattr(instance, "name", None)
    version = getattr(instance, "version", None)
    _write_adapter_load_audit(
        **{
            **base_audit,
            "protocol_check": "passed",
            "adapter_name": name if isinstance(name, str) else None,
            "adapter_version": version if isinstance(version, str) else None,
        },
        load_outcome="loaded",
        error_message=None,
    )
    return instance


def resolve_live_adapter() -> LiveBrokerAdapterProtocol:
    """Top-level resolver. Precedence:

      1. FAKE_LIVE_ADAPTER=true  -> FakeLiveBrokerAdapter, and
                                    LIVE_BROKER_ADAPTER_PATH is NOT
                                    read at all
      2. LIVE_BROKER_ADAPTER_PATH non-empty  -> load_live_adapter(path)
      3. neither set                          -> LiveAdapterLoadError
    """
    use_fake = (os.getenv("FAKE_LIVE_ADAPTER", "") or "").strip().lower() in TRUTHY
    if use_fake:
        return FakeLiveBrokerAdapter()

    path = (os.getenv("LIVE_BROKER_ADAPTER_PATH", "") or "").strip()
    if not path:
        raise LiveAdapterLoadError(
            "no_adapter_source:set_FAKE_LIVE_ADAPTER_or_LIVE_BROKER_ADAPTER_PATH"
        )

    return load_live_adapter(path)
