"""Unit + integration tests for tools.analyze_soak.

The analyzer is the load-bearing piece: a single non-mock row in
trades_soak.jsonl must flip the verdict to FAIL. These tests exercise
both the clean-fixture happy path and a battery of crafted failure
modes (one per critical invariant + one per operational warning).

Quotes / watch jsonl rows are mostly synthesized in-memory to avoid
needing huge fixture files; tests/fixtures/soak_watch_loop_clean.jsonl
is the on-disk canonical schema example loaded by the
`test_clean_fixture_yields_pass_with_matching_meta` test.
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

from tools.analyze_soak import (  # noqa: E402
    FORBIDDEN_LOG_TOKENS,
    REQUIRED_TOP_LEVEL_KEYS,
    SoakReport,
    SoakThresholds,
    analyze_soak,
)

CLEAN_FIXTURE = ROOT / "tests" / "fixtures" / "soak_watch_loop_clean.jsonl"


# ---------------------------------------------------------------------------
# Helpers: synthesise jsonl on disk
# ---------------------------------------------------------------------------

def _row(iter_: int, *, ts_base: float = 1000.0, step: float = 6.0,
         side: str = "LONG", dedupe_action: str = "suppressed_same_key",
         submitted: bool = False) -> dict:
    """Return a watch_loop row whose schema matches Phase 5.D exactly."""
    ts = ts_base + (iter_ - 1) * step
    return {
        "iter": iter_,
        "ts": ts,
        "timestamp": f"2026-05-18T00:00:{(iter_ - 1) * 6:02d}.000+00:00",
        "capture": {"source": "offline:logs/capture_test.png", "shape": [2160, 3840, 3]},
        "detect": {"raw_count": 18, "min_length_ratio": 0.4},
        "filter": {"kept_count": 1, "top_color": "#4CAF50", "top_y": 1533, "top_score": 0.9947},
        "quote": {"status": "ok", "symbol": "XAUUSD", "last": 2250.0, "ts": float(iter_)},
        "calibration": {"status": "ok", "pixel_y": 1533.0},
        "signal": {"side": side, "fibo_line_y": 1533 if side == "LONG" else None,
                   "confidence": 0.896 if side == "LONG" else 0.0,
                   "reason": "fibo touch y=1533 dist=0.0px" if side == "LONG" else "no quote"},
        "dedupe": {"key": "LONG:1533" if side == "LONG" else None,
                   "action": dedupe_action},
        "execution": {"mode": "mock" if submitted else "skipped",
                      "submitted": submitted,
                      "entry": 1533.0 if submitted else None,
                      "reason": None if submitted else "--submit not set"},
        "notify": {"mode": "off", "sent": False},
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def _write_quotes(path: Path, count: int) -> None:
    rows = [
        {"symbol": "XAUUSD", "bid": 2249.75, "ask": 2250.25, "last": 2250.0,
         "mid": 2250.0, "ts": 1000.0 + i, "timestamp": "...", "source": "mock"}
        for i in range(count)
    ]
    _write_jsonl(path, rows)


def _make_loop(rows_count: int, *, first_fired: bool = True,
               all_long: bool = True) -> list[dict]:
    rows = []
    for i in range(1, rows_count + 1):
        side = "LONG" if all_long else ("LONG" if i % 2 else "FLAT")
        if i == 1 and first_fired:
            action = "fired"
        elif side == "FLAT":
            action = "n/a_flat"
        else:
            action = "suppressed_same_key"
        rows.append(_row(i, side=side, dedupe_action=action))
    return rows


# ---------------------------------------------------------------------------
# Critical invariants
# ---------------------------------------------------------------------------

def test_clean_synthesized_60_rows_yields_pass(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)  # 5 min * 60s / 1s

    report = analyze_soak(
        duration_min=5,
        loop_interval_sec=5,
        quote_interval_sec=1,
        watch_log=watch,
        quotes_log=quotes,
        trades_log=None,
        snapshots_log=None,
        submit=False,
        jobs_ok=True,
    )
    assert report.verdict == "PASS", report.to_text()
    assert report.critical_violations == []
    assert report.operational_warnings == []


def test_clean_fixture_yields_pass_with_matching_meta(tmp_path):
    """The on-disk 10-row fixture should pass when paired with the
    duration/interval combination it was generated for: 1 minute /
    6s loop interval / 6s quote interval (10 rows of each). Quote
    fixture is synthesised here to match."""
    quotes = tmp_path / "quotes.jsonl"
    _write_quotes(quotes, 10)
    report = analyze_soak(
        duration_min=1,
        loop_interval_sec=6,
        quote_interval_sec=6,
        watch_log=CLEAN_FIXTURE,
        quotes_log=quotes,
        trades_log=None,
        snapshots_log=None,
        submit=False,
        jobs_ok=True,
    )
    assert report.verdict == "PASS", report.to_text()


# ---- C1: trades mode=mock invariant ----

def test_non_mock_row_in_trades_yields_fail(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    trades = tmp_path / "trades.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(trades, [
        {"ts": 1.0, "side": "LONG", "entry": 1533.0, "mode": "mock"},
        {"ts": 2.0, "side": "LONG", "entry": 1533.0, "mode": "live"},   # offending
        {"ts": 3.0, "side": "LONG", "entry": 1533.0, "mode": "mock"},
    ])
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=trades,
        snapshots_log=None, submit=True, jobs_ok=True,
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C1:") for v in report.critical_violations)


def test_trades_present_without_submit_yields_fail(tmp_path):
    """Dry-run soak that nevertheless wrote trades is a leak."""
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    trades = tmp_path / "trades.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(trades, [{"ts": 1.0, "side": "LONG", "entry": 1533.0, "mode": "mock"}])
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=trades,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C1:") and "without --Submit" in v for v in report.critical_violations)


# ---- C2: jobs ok ----

def test_jobs_not_ok_yields_fail(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=False,
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C2:") for v in report.critical_violations)


# ---- C3: forbidden tokens in captured logs ----

@pytest.mark.parametrize("token", FORBIDDEN_LOG_TOKENS)
def test_forbidden_token_in_captured_log_yields_fail(tmp_path, token):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
        captured_logs_text=f"some output\n{token}\nmore output\n",
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C3:") and token in v for v in report.critical_violations)


# ---- C4: watch_loop row count ----

def test_watch_loop_row_count_below_threshold_yields_fail(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(40))   # expected 60, lower bound 57
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C4:") for v in report.critical_violations)


def test_watch_loop_row_count_within_tolerance_passes(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(58))   # expected 60, lower bound 57 -> OK
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert "C4" not in " ".join(report.critical_violations)


# ---- C5: quote row count ----

def test_quote_row_count_below_threshold_yields_fail(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 100)            # expected 300
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C5:") for v in report.critical_violations)


# ---- C6: schema completeness ----

def test_row_missing_required_key_yields_fail(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    rows = _make_loop(60)
    del rows[5]["dedupe"]   # break schema on iter 6
    _write_jsonl(watch, rows)
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert report.verdict == "FAIL"
    assert any(v.startswith("C6:") and "dedupe" in v for v in report.critical_violations)


def test_all_required_keys_listed_in_module():
    expected = {"iter", "ts", "timestamp", "capture", "detect", "filter",
                "quote", "calibration", "signal", "dedupe", "execution", "notify"}
    assert set(REQUIRED_TOP_LEVEL_KEYS) == expected


# ---------------------------------------------------------------------------
# Operational warnings
# ---------------------------------------------------------------------------

def _snapshots(*, loop_ws=(80, 85, 90), feed_ws=(40, 41, 42),
                loop_cpu=(0.5, 1.0, 1.5), gpu1=(448, 450, 449),
                ts_start: float = 2000.0, step: float = 60.0) -> list[dict]:
    out = []
    for i in range(len(loop_ws)):
        out.append({
            "ts": ts_start + i * step,
            "timestamp": f"2026-05-18T01:{i:02d}:00+00:00",
            "snapshot_idx": i + 1,
            "watch_loop_pid": 12345,
            "quote_feed_pid": 12346,
            "watch_loop_ws_mb": loop_ws[i],
            "quote_feed_ws_mb": feed_ws[i],
            "watch_loop_cpu_sec": loop_cpu[i],
            "quote_feed_cpu_sec": 0.1 * (i + 1),
            "gpu0_mem_mb": 1370, "gpu0_util_pct": 6,
            "gpu1_mem_mb": gpu1[i], "gpu1_util_pct": 0,
            "watch_loop_jsonl_rows": (i + 1) * 12,
            "watch_loop_jsonl_size_kb": (i + 1) * 12,
        })
    return out


def test_loop_ws_growth_above_threshold_yields_degraded(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    snaps = tmp_path / "snaps.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(snaps, _snapshots(loop_ws=(80, 100, 145)))  # 145 - 80 = 65 > 50
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=snaps, submit=False, jobs_ok=True,
    )
    assert report.verdict == "DEGRADED"
    assert any(w.startswith("O1:") for w in report.operational_warnings)


def test_feed_ws_growth_above_threshold_yields_degraded(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    snaps = tmp_path / "snaps.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(snaps, _snapshots(feed_ws=(40, 50, 75)))  # 35 > 30
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=snaps, submit=False, jobs_ok=True,
    )
    assert any(w.startswith("O2:") for w in report.operational_warnings)


def test_dedupe_ratio_low_yields_degraded(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    # 60 rows all "fired" -> ratio = 0
    rows = [_row(i, dedupe_action="fired") for i in range(1, 61)]
    _write_jsonl(watch, rows)
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert any(w.startswith("O4:") for w in report.operational_warnings)


def test_signal_skew_to_flat_yields_degraded(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    # All FLAT
    rows = [_row(i, side="FLAT", dedupe_action="n/a_flat") for i in range(1, 61)]
    _write_jsonl(watch, rows)
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert any(w.startswith("O5:") for w in report.operational_warnings)


def test_timestamps_not_monotonic_yields_degraded(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    rows = _make_loop(60)
    rows[30]["ts"] = rows[29]["ts"] - 10.0   # go backwards
    _write_jsonl(watch, rows)
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert any(w.startswith("O7:") for w in report.operational_warnings)


def test_gpu1_growth_above_threshold_yields_degraded(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    snaps = tmp_path / "snaps.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(snaps, _snapshots(gpu1=(400, 500, 700)))  # 700 - 400 = 300 > 200
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=snaps, submit=False, jobs_ok=True,
    )
    assert any(w.startswith("O8:") for w in report.operational_warnings)


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------

def test_dry_run_with_no_trades_file_is_not_a_violation(tmp_path):
    """Pure dry-run: trades_soak.jsonl absent is the expected state."""
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=tmp_path / "absent.jsonl",
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    assert "C1" not in " ".join(report.critical_violations)


def test_submit_with_all_mock_trades_passes_c1(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    trades = tmp_path / "trades.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(trades, [
        {"ts": float(i), "side": "LONG", "entry": 1533.0, "mode": "mock"}
        for i in range(1, 4)
    ])
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=trades,
        snapshots_log=None, submit=True, jobs_ok=True,
    )
    assert "C1" not in " ".join(report.critical_violations)
    assert report.metrics["trades_count"] == 3
    assert report.metrics["trades_non_mock_count"] == 0


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------

def test_report_to_dict_round_trips_json(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    payload = json.dumps(report.to_dict())
    back = json.loads(payload)
    assert back["verdict"] == report.verdict
    assert set(back) >= {"verdict", "critical_violations", "operational_warnings",
                          "metrics", "duration_min", "submit", "generated_at"}


def test_report_to_text_contains_verdict(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    report = analyze_soak(
        duration_min=5, loop_interval_sec=5, quote_interval_sec=1,
        watch_log=watch, quotes_log=quotes, trades_log=None,
        snapshots_log=None, submit=False, jobs_ok=True,
    )
    text = report.to_text()
    assert "Phase 5 mock soak verdict" in text
    assert f"verdict       : {report.verdict}" in text


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------

def test_cli_writes_log_and_json_report(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    out = tmp_path / "report.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LIVE_TRADING"] = "false"
    env["EXECUTION_MODE"] = "mock"
    env["BROKER_MODE"] = "mock"

    r = subprocess.run(
        [sys.executable, "-m", "tools.analyze_soak",
         "--duration-min", "5",
         "--loop-interval", "5",
         "--quote-interval", "1",
         "--watch-log", str(watch),
         "--quotes-log", str(quotes),
         "--jobs-ok",
         "--out", str(out)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"
    assert out.exists()
    assert out.with_suffix(".json").exists()
    payload = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert payload["verdict"] == "PASS"


def test_cli_returns_nonzero_on_fail(tmp_path):
    watch = tmp_path / "watch.jsonl"
    quotes = tmp_path / "quotes.jsonl"
    trades = tmp_path / "trades.jsonl"
    _write_jsonl(watch, _make_loop(60))
    _write_quotes(quotes, 300)
    _write_jsonl(trades, [{"ts": 1.0, "side": "LONG", "entry": 1533.0, "mode": "live"}])
    out = tmp_path / "report.log"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["LIVE_TRADING"] = "false"
    env["EXECUTION_MODE"] = "mock"
    env["BROKER_MODE"] = "mock"

    r = subprocess.run(
        [sys.executable, "-m", "tools.analyze_soak",
         "--duration-min", "5",
         "--loop-interval", "5",
         "--quote-interval", "1",
         "--watch-log", str(watch),
         "--quotes-log", str(quotes),
         "--trades-log", str(trades),
         "--submit",
         "--jobs-ok",
         "--out", str(out)],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    assert r.returncode == 1, f"expected FAIL exit=1, got {r.returncode}"


# ---------------------------------------------------------------------------
# Module hygiene
# ---------------------------------------------------------------------------

def test_analyzer_module_is_stdlib_only():
    text = (ROOT / "tools" / "analyze_soak.py").read_text(encoding="utf-8")
    forbidden = ["cv2", "numpy", "torch", "ultralytics",
                 "requests", "httpx", "aiohttp", "urllib3", "socket"]
    import re
    for name in forbidden:
        assert not re.search(rf"^\s*(?:import|from)\s+{re.escape(name)}\b",
                              text, re.MULTILINE), \
            f"analyze_soak must stay stdlib-only; found {name}"


def test_analyzer_does_not_import_broker_sdk():
    text = (ROOT / "tools" / "analyze_soak.py").read_text(encoding="utf-8")
    import re
    for sdk in ("shioaji", "ib_insync", "ibapi", "MetaTrader5",
                "ccxt", "binance", "alpaca", "oandapyV20"):
        assert not re.search(rf"^\s*(?:import|from)\s+{re.escape(sdk)}\b",
                              text, re.MULTILINE), \
            f"analyze_soak must not import broker SDK {sdk}"


def test_analyzer_does_not_import_executor_or_risk_or_strategy():
    text = (ROOT / "tools" / "analyze_soak.py").read_text(encoding="utf-8")
    import re
    for pkg in ("executor", "risk", "strategy"):
        assert not re.search(rf"^\s*(?:import|from)\s+{re.escape(pkg)}(\.|\s)",
                              text, re.MULTILINE), \
            f"analyze_soak must not import trade-origin package {pkg}"
