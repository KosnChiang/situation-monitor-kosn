"""AI decision JSON validator + audit log writer (Phase 6.B-1).

Parses either a JSON string or a dict, checks required fields,
types, value ranges, and emits a single record to
``logs/ai_decisions.jsonl`` (env: ``AI_DECISIONS_LOG``) regardless
of validity. Invalid input is logged with errors; valid input is
logged with the parsed AIDecision.

``tradable`` is True only when:
  * the decision is valid,
  * side is LONG or SHORT (FLAT and WATCH are explicitly non-tradable),
  * confidence >= the configured trade threshold.

Strictly mock-only:
  * No broker SDK, no outbound HTTP, no broker credential env read.
  * Imports only stdlib + ai_decision.models.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ai_decision.models import AIDecision, VALID_SIDES


REQUIRED_FIELDS = (
    "decision_id", "symbol", "side", "entry", "stop", "target",
    "confidence", "reason",
)


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    tradable: bool
    decision: Optional[AIDecision]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def default_log_path() -> Path:
    return Path(os.getenv("AI_DECISIONS_LOG", "logs/ai_decisions.jsonl"))


def _emit_log(
    *,
    valid: bool,
    tradable: bool,
    errors: list[str],
    warnings: list[str],
    decision: Optional[AIDecision],
    raw_summary: Optional[dict],
    log_path: Path,
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_type": "ai_decision_validation",
        "ts": time.time(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "valid": valid,
        "tradable": tradable,
        "errors": errors,
        "warnings": warnings,
        "decision": decision.to_log_dict() if decision is not None else None,
        "raw_summary": raw_summary,
    }
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def validate_ai_decision(
    raw: Any,
    *,
    min_trade_confidence: float = 0.7,
    log_path: Optional[Path] = None,
) -> ValidationResult:
    """Validate an AI decision payload and write one audit row."""
    target_path = log_path or default_log_path()
    errors: list[str] = []
    warnings: list[str] = []

    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            result = ValidationResult(
                valid=False, tradable=False, decision=None,
                errors=[f"json_decode:{e}"], warnings=[],
            )
            _emit_log(
                valid=False, tradable=False, errors=result.errors,
                warnings=[], decision=None,
                raw_summary={"raw_str_prefix": raw[:200]},
                log_path=target_path,
            )
            return result
    elif isinstance(raw, dict):
        data = dict(raw)
    else:
        result = ValidationResult(
            valid=False, tradable=False, decision=None,
            errors=[f"unsupported_input_type:{type(raw).__name__}"],
            warnings=[],
        )
        _emit_log(
            valid=False, tradable=False, errors=result.errors,
            warnings=[], decision=None,
            raw_summary={"input_type": type(raw).__name__},
            log_path=target_path,
        )
        return result

    for field_name in REQUIRED_FIELDS:
        if field_name not in data:
            errors.append(f"missing_field:{field_name}")

    side = data.get("side")
    if not isinstance(side, str) or side not in VALID_SIDES:
        errors.append(f"invalid_side:{side!r}")

    for numeric_field in ("entry", "stop", "target", "confidence"):
        val = data.get(numeric_field)
        if val is not None and not isinstance(val, (int, float)):
            errors.append(f"non_numeric:{numeric_field}:{val!r}")

    conf = data.get("confidence")
    if isinstance(conf, (int, float)) and (conf < 0.0 or conf > 1.0):
        errors.append(f"confidence_out_of_range:{conf}")

    if errors:
        _emit_log(
            valid=False, tradable=False, errors=errors,
            warnings=warnings, decision=None,
            raw_summary={k: data.get(k) for k in data if k != "secret"},
            log_path=target_path,
        )
        return ValidationResult(
            valid=False, tradable=False, decision=None,
            errors=errors, warnings=warnings,
        )

    ts = float(data.get("ts", time.time()))
    iso = data.get("timestamp", datetime.now(timezone.utc).isoformat())
    decision = AIDecision(
        decision_id=str(data["decision_id"]),
        ts=ts,
        timestamp=str(iso),
        symbol=str(data["symbol"]),
        side=str(side),
        entry=float(data["entry"]),
        stop=float(data["stop"]),
        target=float(data["target"]),
        confidence=float(conf if conf is not None else 0.0),
        reason=str(data["reason"]),
        invalidation=str(data.get("invalidation", "")),
        data_sources=list(data.get("data_sources", [])),
        mode=str(data.get("mode", "mock")),
    )

    tradable = (
        decision.side in ("LONG", "SHORT")
        and decision.confidence >= min_trade_confidence
    )
    if decision.side in ("LONG", "SHORT") and decision.confidence < min_trade_confidence:
        warnings.append(
            f"confidence_below_trade_threshold:{decision.confidence}<{min_trade_confidence}"
        )
    if decision.side in ("FLAT", "WATCH"):
        warnings.append(f"side_not_tradable:{decision.side}")

    _emit_log(
        valid=True, tradable=tradable, errors=[],
        warnings=warnings, decision=decision, raw_summary=None,
        log_path=target_path,
    )
    return ValidationResult(
        valid=True, tradable=tradable, decision=decision,
        errors=[], warnings=warnings,
    )
