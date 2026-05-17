"""Smoke tests for the FastAPI app. Skipped if fastapi is not installed
(e.g. before `pip install -r requirements.txt`)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["LIVE_TRADING"] = "false"
os.environ["EXECUTION_MODE"] = "mock"
os.environ["TRADES_LOG"] = str(ROOT / "logs" / "trades.test.jsonl")

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["live_trading"] == "false"
    assert data["execution_mode"] == "mock"


def test_signal_accepts_long():
    r = client.post(
        "/signal",
        json={
            "side": "LONG",
            "entry": 100.0,
            "stop": 99.0,
            "target": 110.0,
            "confidence": 0.8,
            "reason": "api-test",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["mode"] == "mock"
    assert body["fill"]["side"] == "LONG"


def test_signal_rejects_flat():
    r = client.post(
        "/signal",
        json={
            "side": "FLAT",
            "entry": 100.0,
            "stop": 100.0,
            "target": 100.0,
            "confidence": 0.9,
        },
    )
    assert r.status_code == 409
