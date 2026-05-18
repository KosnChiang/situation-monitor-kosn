"""End-to-end Phase 5.D tests for tools.watch_fibo_loop.

All tests use the ``--offline-image`` capture path so mss is never
imported. The chart is generated on the fly with cv2 + numpy and
written to tmp_path; the demo calibration fixture maps the synthetic
line's y=240 onto a clean $2300.0 quote so LONG / FLAT outcomes are
deterministic.

Coverage:

  * refuse-to-start on hot env
  * argparse mutual-exclusion + defaults
  * capture failure modes (missing PNG)
  * detect / filter degeneracies (blank, all-grey)
  * quote failure modes (missing / empty / malformed)
  * calibration absence (fallback) and invalidity (FLAT)
  * full pipeline with --submit lands one mock trade in TRADES_LOG
  * dedupe suppresses repeated identical signals; cooldown=0 re-fires
  * --dedupe-from-trades-log seeds suppression from prior fills
  * notify=off records no send; notify=dry emits a dry-run record;
    state-transition gate skips notify on identical consecutive sigs
  * watch_loop.jsonl row schema matches the fixture
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("LIVE_TRADING", "false")
os.environ.setdefault("EXECUTION_MODE", "mock")
os.environ.setdefault("BROKER_MODE", "mock")

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from tools.watch_fibo_loop import (  # noqa: E402
    WatchLoopConfig,
    WatchLoopRunner,
    _parse_args,
    _refuse_if_live,
)


CAL_DEMO   = ROOT / "tests" / "fixtures" / "watch_loop_calibration_demo.yaml"
ROW_SCHEMA = ROOT / "tests" / "fixtures" / "watch_loop_row_schema.json"

# Bright Material green in BGR (RGB = #4CAF50). Hand-picked so the
# filter's palette bonus fires.
GREEN_BGR = (80, 175, 76)


def _green_line_chart(y: int = 240, *, w: int = 800, h: int = 480):
    img = np.full((h, w, 3), 25, dtype=np.uint8)
    rng = np.random.default_rng(7)
    for x in range(0, w, 12):
        bar_h = int(rng.integers(20, 200))
        y0 = int(rng.integers(20, h - 20 - bar_h))
        color = (180, 180, 180) if rng.random() > 0.5 else (90, 90, 90)
        cv2.rectangle(img, (x + 2, y0), (x + 8, y0 + bar_h), color, -1)
    cv2.line(img, (int(w * 0.05), y), (int(w * 0.95), y), GREEN_BGR, 2)
    return img


def _blank_chart(w: int = 800, h: int = 480):
    return np.full((h, w, 3), 25, dtype=np.uint8)


def _grey_lines_chart(*, w: int = 800, h: int = 480):
    """Detector should find these (high gradient) but filter rejects
    them on Rule C (chroma=0). Result: raw_count > 0, kept_count == 0."""
    img = _blank_chart(w, h)
    GREY = (150, 150, 150)
    for y in (60, 120, 180, 240, 300, 360):
        cv2.line(img, (int(w * 0.05), y), (int(w * 0.95), y), GREY, 2)
    return img


def _write_png(img, p: Path) -> str:
    assert cv2.imwrite(str(p), img)
    return str(p)


def _write_quote(p: Path, last: float, *, symbol: str = "XAUUSD") -> None:
    rec = {
        "symbol": symbol,
        "bid": last - 0.25, "ask": last + 0.25, "last": last, "mid": last,
        "ts": 1.0, "timestamp": "2026-05-18T00:00:00+00:00", "source": "mock",
    }
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def _cfg(tmp_path: Path, **overrides) -> WatchLoopConfig:
    """Default config: green-line chart + demo calibration + 1 iteration, no submit, notify off."""
    base = dict(
        offline_image=_write_png(_green_line_chart(240), tmp_path / "chart.png"),
        config_path=str(CAL_DEMO),
        quotes_file=str(tmp_path / "quotes.jsonl"),
        watch_log=str(tmp_path / "watch_loop.jsonl"),
        interval_sec=0.0,
        max_iterations=1,
        submit=False,
        notify_mode="off",
        symbol="XAUUSD",
    )
    base.update(overrides)
    return WatchLoopConfig(**base)


# ---------------------------------------------------------------------------
# refuse-to-start
# ---------------------------------------------------------------------------

def test_refuse_when_live_trading_true(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "mock")
    with pytest.raises(SystemExit) as exc:
        _refuse_if_live()
    assert "LIVE_TRADING" in str(exc.value)


def test_refuse_when_execution_mode_not_mock(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "live")
    with pytest.raises(SystemExit) as exc:
        _refuse_if_live()
    assert "EXECUTION_MODE" in str(exc.value)


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def test_parse_args_requires_offline_image_or_live_capture():
    with pytest.raises(SystemExit):
        _parse_args([])


def test_default_args_have_submit_off_and_notify_off(tmp_path):
    img = _write_png(_green_line_chart(240), tmp_path / "c.png")
    cfg = _parse_args(["--offline-image", img])
    assert cfg.submit is False
    assert cfg.notify_mode == "off"
    assert cfg.dedupe_from_trades_log is False
    assert cfg.live_capture is False
    assert cfg.offline_image == img


def test_runner_rejects_both_offline_and_live(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        WatchLoopRunner(WatchLoopConfig(
            offline_image=str(tmp_path / "x.png"),
            live_capture=True,
            max_iterations=1,
        ))


# ---------------------------------------------------------------------------
# capture failure
# ---------------------------------------------------------------------------

def test_missing_offline_image_logs_error_and_continues(tmp_path):
    cfg = _cfg(tmp_path, offline_image=str(tmp_path / "doesnotexist.png"))
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert "error" in rec["capture"]
    assert rec["signal"]["side"] == "FLAT"
    assert rec["signal"]["reason"] == "no frame"
    assert rec["execution"]["submitted"] is False


# ---------------------------------------------------------------------------
# detect / filter degeneracies
# ---------------------------------------------------------------------------

def test_empty_detection_yields_flat_record(tmp_path):
    p = _write_png(_blank_chart(), tmp_path / "blank.png")
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, offline_image=p)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["detect"]["raw_count"] == 0
    assert rec["filter"]["kept_count"] == 0
    assert rec["signal"]["side"] == "FLAT"


def test_filter_dropping_everything_yields_flat_record(tmp_path):
    p = _write_png(_grey_lines_chart(), tmp_path / "grey.png")
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, offline_image=p)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["detect"]["raw_count"] > 0
    assert rec["filter"]["kept_count"] == 0
    assert rec["signal"]["side"] == "FLAT"
    assert "no fibo lines kept after filter" in rec["signal"]["reason"]


# ---------------------------------------------------------------------------
# quote failure modes
# ---------------------------------------------------------------------------

def test_missing_quote_file_yields_flat_record(tmp_path):
    cfg = _cfg(tmp_path)
    assert not Path(cfg.quotes_file).exists()
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["quote"]["status"] == "file_missing"
    assert rec["signal"]["side"] == "FLAT"


def test_empty_quote_file_yields_flat_record(tmp_path):
    (tmp_path / "quotes.jsonl").write_text("", encoding="utf-8")
    cfg = _cfg(tmp_path)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["quote"]["status"] == "empty"
    assert rec["signal"]["side"] == "FLAT"


def test_malformed_quote_yields_flat_record(tmp_path):
    (tmp_path / "quotes.jsonl").write_text("not json\n", encoding="utf-8")
    cfg = _cfg(tmp_path)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["quote"]["status"] == "malformed"
    assert rec["signal"]["side"] == "FLAT"


# ---------------------------------------------------------------------------
# calibration handling
# ---------------------------------------------------------------------------

def test_missing_calibration_config_falls_back_to_raw_pixel_y(tmp_path):
    no_cfg = tmp_path / "absent.yaml"
    assert not no_cfg.exists()
    # raw fallback treats last as pixel_y. Detected y ~ 240; quote 240 -> match.
    _write_quote(tmp_path / "quotes.jsonl", last=240.0)
    cfg = _cfg(tmp_path, config_path=str(no_cfg))
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["calibration"]["status"] == "missing_config"
    assert rec["signal"]["side"] == "LONG"


def test_invalid_calibration_block_emits_flat_and_does_not_crash(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "calibration:\n"
        "  reference_high:\n    pixel_y: 440\n    price: 2400.0\n"  # inverted axis
        "  reference_low:\n    pixel_y: 40\n    price: 2200.0\n",
        encoding="utf-8",
    )
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, config_path=str(bad))
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["calibration"]["status"] == "invalid"
    assert rec["signal"]["side"] == "FLAT"
    assert "calibration invalid" in rec["signal"]["reason"]


# ---------------------------------------------------------------------------
# full pipeline + submit + dedupe
# ---------------------------------------------------------------------------

def test_full_offline_pipeline_fires_long_once_with_submit(tmp_path, monkeypatch):
    trades = tmp_path / "trades.jsonl"
    monkeypatch.setenv("TRADES_LOG", str(trades))
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")

    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, submit=True)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)

    assert rec["signal"]["side"] == "LONG"
    assert abs(rec["signal"]["fibo_line_y"] - 240) <= 3
    assert rec["dedupe"]["action"] == "fired"
    assert rec["execution"]["submitted"] is True
    assert rec["execution"]["mode"] == "mock"

    fills = [json.loads(r) for r in trades.read_text(encoding="utf-8").strip().splitlines()]
    assert len(fills) == 1
    assert fills[0]["mode"] == "mock"
    assert fills[0]["side"] == "LONG"


def test_dedupe_repeated_iterations_same_signal_submit_once(tmp_path, monkeypatch):
    trades = tmp_path / "trades.jsonl"
    monkeypatch.setenv("TRADES_LOG", str(trades))
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")

    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, submit=True, max_iterations=5, dedupe_cooldown_sec=3600)
    WatchLoopRunner(cfg).run()

    recs = [json.loads(r) for r in Path(cfg.watch_log).read_text(encoding="utf-8").strip().splitlines()]
    assert len(recs) == 5
    fired = [r for r in recs if r["dedupe"]["action"] == "fired"]
    suppressed = [r for r in recs if r["dedupe"]["action"] == "suppressed_same_key"]
    assert len(fired) == 1
    assert len(suppressed) == 4

    fills = trades.read_text(encoding="utf-8").strip().splitlines()
    assert len(fills) == 1


def test_cooldown_zero_re_submits_each_iteration(tmp_path, monkeypatch):
    trades = tmp_path / "trades.jsonl"
    monkeypatch.setenv("TRADES_LOG", str(trades))
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")

    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, submit=True, max_iterations=3, dedupe_cooldown_sec=0)
    WatchLoopRunner(cfg).run()

    fills = trades.read_text(encoding="utf-8").strip().splitlines()
    assert len(fills) == 3


def test_dedupe_preload_from_trades_log_suppresses_first_iteration(tmp_path, monkeypatch):
    import time as _time
    trades = tmp_path / "trades.jsonl"
    monkeypatch.setenv("TRADES_LOG", str(trades))
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")

    # Pre-seed RECENT fills covering every y the detector might pick
    # around 240; with cooldown=300s and ts=now-1s, the preload should
    # bring them into the dedupe table and suppress an identical fresh
    # signal on the next iteration.
    recent_ts = _time.time() - 1.0
    priors = [
        {"ts": recent_ts, "side": "LONG", "entry": float(y), "stop": float(y) + 20,
         "target": float(y) - 40, "confidence": 0.9, "reason": "prior", "mode": "mock"}
        for y in range(235, 246)
    ]
    trades.write_text(
        "\n".join(json.dumps(p) for p in priors) + "\n",
        encoding="utf-8",
    )

    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, submit=True, max_iterations=1,
               dedupe_from_trades_log=True,
               dedupe_cooldown_sec=300)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)

    assert rec["signal"]["side"] == "LONG"
    assert rec["dedupe"]["action"] == "suppressed_same_key"
    assert rec["execution"]["submitted"] is False

    # trades.jsonl still has only the priors, no new row.
    fills = trades.read_text(encoding="utf-8").strip().splitlines()
    assert len(fills) == len(priors)


def test_dedupe_preload_ignores_fills_older_than_cooldown(tmp_path, monkeypatch):
    """Old fills should NOT preload -- a loop restarted hours after the
    last trade must be free to re-fire."""
    trades = tmp_path / "trades.jsonl"
    monkeypatch.setenv("TRADES_LOG", str(trades))
    monkeypatch.setenv("LIVE_TRADING", "false")
    monkeypatch.setenv("EXECUTION_MODE", "mock")

    ancient = 100.0  # epoch second 100, decades ago
    priors = [
        {"ts": ancient, "side": "LONG", "entry": float(y), "stop": float(y) + 20,
         "target": float(y) - 40, "confidence": 0.9, "reason": "ancient", "mode": "mock"}
        for y in range(235, 246)
    ]
    trades.write_text("\n".join(json.dumps(p) for p in priors) + "\n", encoding="utf-8")

    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, submit=True, max_iterations=1,
               dedupe_from_trades_log=True,
               dedupe_cooldown_sec=60)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)

    assert rec["signal"]["side"] == "LONG"
    assert rec["dedupe"]["action"] == "fired"
    assert rec["execution"]["submitted"] is True


# ---------------------------------------------------------------------------
# notify
# ---------------------------------------------------------------------------

def test_notify_off_records_no_send(tmp_path):
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, notify_mode="off")
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["notify"]["mode"] == "off"
    assert rec["notify"]["sent"] is False


def test_notify_dry_run_emits_dry_run_record(tmp_path):
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, notify_mode="dry")
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    assert rec["notify"]["mode"] == "dry"
    # state transition None -> LONG triggers notify
    assert rec["notify"]["sent"] is True
    assert rec["notify"]["dry_run"] is True


def test_notify_only_on_state_transition(tmp_path):
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, notify_mode="dry", max_iterations=2)
    WatchLoopRunner(cfg).run()
    recs = [json.loads(r) for r in Path(cfg.watch_log).read_text(encoding="utf-8").strip().splitlines()]
    assert len(recs) == 2
    assert recs[0]["notify"]["sent"] is True
    assert recs[1]["notify"]["sent"] is False
    assert recs[1]["notify"].get("reason") == "no state transition"


# ---------------------------------------------------------------------------
# log / schema / exit
# ---------------------------------------------------------------------------

def test_watch_loop_jsonl_has_one_row_per_iteration(tmp_path):
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, max_iterations=3)
    WatchLoopRunner(cfg).run()
    rows = Path(cfg.watch_log).read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 3
    for line in rows:
        rec = json.loads(line)
        assert "iter" in rec


def test_row_has_required_top_level_keys(tmp_path):
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path)
    rec = WatchLoopRunner(cfg).run_one_iteration(1)
    schema = json.loads(ROW_SCHEMA.read_text(encoding="utf-8"))
    missing = [k for k in schema["required_top_level_keys"] if k not in rec]
    assert not missing, f"missing keys: {missing}"


def test_max_iterations_terminates_cleanly(tmp_path):
    _write_quote(tmp_path / "quotes.jsonl", last=2300.0)
    cfg = _cfg(tmp_path, max_iterations=2)
    rc = WatchLoopRunner(cfg).run()
    assert rc == 0
    rows = Path(cfg.watch_log).read_text(encoding="utf-8").strip().splitlines()
    assert len(rows) == 2
