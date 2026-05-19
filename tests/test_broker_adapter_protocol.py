"""Protocol conformance tests for Phase 6.A-2 broker adapters."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"

from executor.broker_adapter import (  # noqa: E402
    BrokerAdapter,
    MockBrokerAdapter,
    PaperBrokerAdapter,
)
from executor.mock_executor import MockExecutor  # noqa: E402
from strategy.fibo_mob_v2 import Signal  # noqa: E402


def test_mock_adapter_has_name_mock():
    assert MockBrokerAdapter().name == "mock"


def test_paper_adapter_has_name_paper():
    assert PaperBrokerAdapter().name == "paper"


def test_mock_adapter_satisfies_protocol():
    assert isinstance(MockBrokerAdapter(), BrokerAdapter)


def test_paper_adapter_satisfies_protocol():
    assert isinstance(PaperBrokerAdapter(), BrokerAdapter)


def test_mock_adapter_delegates_to_passed_executor(tmp_path):
    log = tmp_path / "trades.jsonl"
    adapter = MockBrokerAdapter(MockExecutor(log_path=str(log)))
    fill = adapter.submit(Signal("LONG", 100.0, 99.0, 110.0, 0.8, "delegate"))
    assert fill.side == "LONG"
    assert fill.mode == "mock"
    assert log.exists()
    content = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(content) == 1


def test_mock_adapter_creates_default_executor_when_none_passed():
    adapter = MockBrokerAdapter()
    assert hasattr(adapter, "_executor")
    assert adapter._executor is not None
