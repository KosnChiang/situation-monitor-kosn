"""Behavioural tests for the read-only quote feed.

Covers the read side only:

  * ``Quote`` dataclass shape (symbol / bid / ask / last / ts /
    timestamp / source / mid).
  * ``MockQuoteProvider`` deterministic random walk under a fixed seed.
  * ``QuoteProvider.__init__`` refuses to construct when
    ``LIVE_TRADING != "false"``.
  * ``tools.quote_feed`` CLI writes exactly the rows it claimed to
    write and creates no other file.
  * ``tools.mock_fibo_signal --price-source latest_quote`` tails the
    JSONL file written by the quote feed and emits FLAT when the
    file is missing or empty.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")
os.environ.setdefault("BROKER_MODE", "mock")


# -------------------------------------------------------------- in-process

def test_quote_dataclass_shape_and_mid():
    from quote.quote_provider import Quote
    q = Quote(symbol="XAUUSD", bid=2399.5, ask=2400.5, last=2400.0,
              ts=1.0, timestamp="2026-05-17T00:00:00+00:00", source="mock")
    assert q.mid == pytest.approx(2400.0)
    d = q.to_dict()
    for k in ("symbol", "bid", "ask", "last", "mid", "ts", "timestamp", "source"):
        assert k in d


def test_mock_provider_seeded_walk_is_deterministic():
    from quote.quote_provider import MockQuoteProvider
    a = MockQuoteProvider(seed=42, base_price=100.0, volatility=1.0)
    b = MockQuoteProvider(seed=42, base_price=100.0, volatility=1.0)
    for _ in range(5):
        qa = a.fetch("X")
        qb = b.fetch("X")
        assert qa.last == qb.last
        assert qa.bid == qb.bid
        assert qa.ask == qb.ask
        assert qa.source == "mock"


def test_mock_provider_advances_state():
    from quote.quote_provider import MockQuoteProvider
    p = MockQuoteProvider(seed=1, base_price=100.0, volatility=1.0)
    first = p.fetch("X").last
    second = p.fetch("X").last
    # Almost certainly different under a non-zero volatility
    assert first != second


def test_provider_refuses_live_trading(monkeypatch):
    from quote.quote_provider import MockQuoteProvider, QuoteFeedForbidden
    monkeypatch.setenv("LIVE_TRADING", "true")
    with pytest.raises(QuoteFeedForbidden):
        MockQuoteProvider(seed=1)


# -------------------------------------------------------------- CLI integration

def _run_quote_feed(*, symbol: str, output: Path, iterations: int,
                    interval: float = 0.0, seed: int = 1):
    return subprocess.run(
        [sys.executable, "-m", "tools.quote_feed",
         "--symbol", symbol,
         "--provider", "mock",
         "--interval", str(interval),
         "--output", str(output),
         "--max-iterations", str(iterations),
         "--seed", str(seed)],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**os.environ,
             "LIVE_TRADING": "false",
             "EXECUTION_MODE": "mock",
             "BROKER_MODE": "mock"},
    )


def test_quote_feed_writes_expected_number_of_rows(tmp_path):
    out = tmp_path / "quotes.jsonl"
    r = _run_quote_feed(symbol="XAUUSD", output=out, iterations=3)
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 3
    for row in rows:
        for k in ("symbol", "bid", "ask", "last", "mid", "ts", "timestamp", "source"):
            assert k in row
        assert row["symbol"] == "XAUUSD"
        assert row["source"] == "mock"
        assert row["bid"] < row["last"] < row["ask"] or row["bid"] <= row["last"] <= row["ask"]


def test_quote_feed_creates_only_quotes_jsonl(tmp_path):
    """Item 4 from spec: quote module only writes logs/quotes.jsonl."""
    out = tmp_path / "quotes.jsonl"
    before = set(tmp_path.iterdir())
    r = _run_quote_feed(symbol="XAUUSD", output=out, iterations=2)
    assert r.returncode == 0, f"stderr={r.stderr}"
    after = set(tmp_path.iterdir())
    new_files = after - before
    assert new_files == {out}, f"unexpected new files: {new_files}"


def test_quote_feed_refuses_live_trading(tmp_path):
    out = tmp_path / "quotes.jsonl"
    r = subprocess.run(
        [sys.executable, "-m", "tools.quote_feed",
         "--symbol", "XAUUSD", "--provider", "mock",
         "--output", str(out), "--max-iterations", "1"],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**os.environ, "LIVE_TRADING": "true", "EXECUTION_MODE": "mock", "BROKER_MODE": "mock"},
    )
    assert r.returncode != 0
    assert "LIVE_TRADING" in (r.stderr + r.stdout)
    assert not out.exists() or out.read_text(encoding="utf-8") == ""


# -------------------------------------------------------------- IPC contract

def test_mock_fibo_signal_latest_quote_reads_last_row(tmp_path):
    """The signal generator tails the quote feed's JSONL by file path
    only -- the two processes share no in-memory state."""
    fibo = tmp_path / "fibo_lines.json"
    quotes = tmp_path / "quotes.jsonl"
    signals = tmp_path / "signals.jsonl"
    trades = tmp_path / "trades.jsonl"

    # A Fibo line at y=2401 will be within tolerance of last=2400.5 below.
    fibo.write_text(json.dumps({
        "input": "synthetic", "image_shape": [480, 800], "count": 1,
        "lines": [{"y": 2401, "x_start": 10, "x_end": 700, "length": 690,
                    "color_bgr": [60, 255, 60], "color_rgb": [60, 255, 60],
                    "color_hex": "#3CFF3C", "angle_deg": 0.0, "confidence": 0.9}],
    }), encoding="utf-8")
    quotes.write_text(
        "\n".join([
            json.dumps({"symbol": "X", "bid": 2399.5, "ask": 2400.5, "last": 2400.0,
                        "mid": 2400.0, "ts": 1.0, "timestamp": "2026-05-17T00:00:00+00:00",
                        "source": "mock"}),
            json.dumps({"symbol": "X", "bid": 2400.0, "ask": 2401.0, "last": 2400.5,
                        "mid": 2400.5, "ts": 2.0, "timestamp": "2026-05-17T00:00:01+00:00",
                        "source": "mock"}),
        ]) + "\n",
        encoding="utf-8",
    )

    r = subprocess.run(
        [sys.executable, "-m", "tools.mock_fibo_signal",
         "--fibo-lines", str(fibo),
         "--out-signals", str(signals),
         "--price-source", "latest_quote",
         "--quotes-file", str(quotes),
         "--symbol", "X",
         "--tolerance-px", "5",
         "--min-confidence", "0.55"],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**os.environ, "LIVE_TRADING": "false", "EXECUTION_MODE": "mock",
             "BROKER_MODE": "mock", "TRADES_LOG": str(trades)},
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "LONG"
    assert rec["fibo_line_y"] == 2401


def test_mock_fibo_signal_latest_quote_missing_file_is_flat(tmp_path):
    """spec: 找不到 quote 時回 FLAT (and must not crash)."""
    fibo = tmp_path / "fibo_lines.json"
    fibo.write_text(json.dumps({"lines": []}), encoding="utf-8")
    signals = tmp_path / "signals.jsonl"
    quotes_missing = tmp_path / "quotes_that_do_not_exist.jsonl"

    r = subprocess.run(
        [sys.executable, "-m", "tools.mock_fibo_signal",
         "--fibo-lines", str(fibo),
         "--out-signals", str(signals),
         "--price-source", "latest_quote",
         "--quotes-file", str(quotes_missing),
         "--symbol", "X"],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**os.environ, "LIVE_TRADING": "false", "EXECUTION_MODE": "mock", "BROKER_MODE": "mock"},
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "FLAT"
    assert "no price" in rec["reason"].lower() or "does not exist" in rec["reason"].lower()


def test_mock_fibo_signal_latest_quote_empty_file_is_flat(tmp_path):
    fibo = tmp_path / "fibo_lines.json"
    fibo.write_text(json.dumps({"lines": []}), encoding="utf-8")
    signals = tmp_path / "signals.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    quotes.write_text("", encoding="utf-8")

    r = subprocess.run(
        [sys.executable, "-m", "tools.mock_fibo_signal",
         "--fibo-lines", str(fibo),
         "--out-signals", str(signals),
         "--price-source", "latest_quote",
         "--quotes-file", str(quotes),
         "--symbol", "X"],
        cwd=str(ROOT), capture_output=True, text=True,
        env={**os.environ, "LIVE_TRADING": "false", "EXECUTION_MODE": "mock", "BROKER_MODE": "mock"},
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    rec = json.loads(signals.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert rec["side"] == "FLAT"
