"""Unit tests for ai_decision.validator (Phase 6.B-1)."""
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

from ai_decision.models import AIDecision  # noqa: E402
from ai_decision.validator import (  # noqa: E402
    ValidationResult,
    validate_ai_decision,
)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _valid_payload(**overrides) -> dict:
    base = {
        "decision_id": "ai-1",
        "ts": 1779000000.0,
        "timestamp": "2026-05-19T00:00:00+00:00",
        "symbol": "XAUUSD",
        "side": "LONG",
        "entry": 23010.5,
        "stop": 22980.0,
        "target": 23090.0,
        "confidence": 0.78,
        "reason": "fibo MOB at 0.618 + RSI divergence",
        "invalidation": "close below 22980",
        "data_sources": ["fibo_lines_filtered.json", "quotes.jsonl"],
        "mode": "mock",
    }
    base.update(overrides)
    return base


def test_valid_long_decision(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(), log_path=p)
    assert r.valid is True
    assert r.tradable is True
    assert isinstance(r.decision, AIDecision)
    assert r.decision.side == "LONG"
    rows = _read_jsonl(p)
    assert rows
    assert rows[-1]["valid"] is True
    assert rows[-1]["decision"]["side"] == "LONG"


def test_valid_short_decision(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(side="SHORT"), log_path=p)
    assert r.valid is True
    assert r.tradable is True
    assert r.decision.side == "SHORT"


def test_valid_flat_is_non_tradable(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(side="FLAT", confidence=0.9), log_path=p)
    assert r.valid is True
    assert r.tradable is False
    assert any("side_not_tradable" in w for w in r.warnings)


def test_valid_watch_is_non_tradable(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(side="WATCH", confidence=0.9), log_path=p)
    assert r.valid is True
    assert r.tradable is False
    assert any("side_not_tradable" in w for w in r.warnings)


def test_missing_decision_id_invalid(tmp_path):
    p = tmp_path / "ai.jsonl"
    payload = _valid_payload()
    payload.pop("decision_id")
    r = validate_ai_decision(payload, log_path=p)
    assert r.valid is False
    assert any("decision_id" in e for e in r.errors)


def test_missing_side_invalid(tmp_path):
    p = tmp_path / "ai.jsonl"
    payload = _valid_payload()
    payload.pop("side")
    r = validate_ai_decision(payload, log_path=p)
    assert r.valid is False
    assert any("side" in e for e in r.errors)


def test_invalid_side_value(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(side="GARBAGE"), log_path=p)
    assert r.valid is False
    assert any("invalid_side" in e for e in r.errors)


def test_confidence_out_of_range_invalid(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(confidence=1.5), log_path=p)
    assert r.valid is False
    assert any("confidence_out_of_range" in e for e in r.errors)


def test_non_numeric_entry_invalid(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(_valid_payload(entry="not-a-number"), log_path=p)
    assert r.valid is False
    assert any("non_numeric" in e for e in r.errors)


def test_low_confidence_long_is_valid_but_not_tradable(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(
        _valid_payload(confidence=0.3), log_path=p, min_trade_confidence=0.7,
    )
    assert r.valid is True
    assert r.tradable is False
    assert any("confidence_below_trade_threshold" in w for w in r.warnings)


def test_malformed_json_string_invalid(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision("not json at all", log_path=p)
    assert r.valid is False
    assert any("json_decode" in e for e in r.errors)


def test_unsupported_input_type_invalid(tmp_path):
    p = tmp_path / "ai.jsonl"
    r = validate_ai_decision(12345, log_path=p)
    assert r.valid is False
    assert any("unsupported_input_type" in e for e in r.errors)


def test_invalid_decision_still_writes_audit_row(tmp_path):
    p = tmp_path / "ai.jsonl"
    payload = _valid_payload()
    payload.pop("side")
    validate_ai_decision(payload, log_path=p)
    rows = _read_jsonl(p)
    assert rows
    assert rows[-1]["valid"] is False
    assert rows[-1]["decision"] is None
    assert rows[-1]["raw_summary"] is not None


def test_json_string_input_is_parsed(tmp_path):
    p = tmp_path / "ai.jsonl"
    raw = json.dumps(_valid_payload())
    r = validate_ai_decision(raw, log_path=p)
    assert r.valid is True
    assert r.decision.side == "LONG"
