"""Mock-only watch loop: capture -> detect -> filter -> signal -> [submit] -> [notify] -> log.

Composes the existing read-only pieces -- screen / offline image capture,
the OpenCV Fibo line detector, the Phase-5.B colour/position filter,
chart calibration, the latest-quote tail, and the Phase-5 mock signal
generator -- into one orchestrator that runs on a fixed cadence and
writes one structured row per iteration to ``logs/watch_loop.jsonl``.

Design constraints, enforced by tests/test_no_live_trading_phase5d.py
and tests/test_watch_fibo_loop.py:

  * Refuses to start unless ``LIVE_TRADING=false`` and
    ``EXECUTION_MODE=mock``.
  * ``--submit`` is opt-in. The default loop only writes
    ``logs/watch_loop.jsonl`` and never touches ``RiskGate`` /
    ``MockExecutor``.
  * ``--notify-mode`` defaults to ``off``; even ``dry`` never networks
    (Telegram has its own dry-run path) and ``live`` would only fire
    on state transitions, not every iteration.
  * Module-top imports are stdlib + ``vision.*`` + ``tools.mock_fibo_signal``
    only. ``executor`` / ``risk`` / ``strategy`` / ``mss`` / ``requests``
    are loaded lazily inside the ``--submit`` / ``--live-capture`` /
    ``--notify-mode != off`` code paths so the default invocation
    cannot transitively pull a trade-origin module into RAM.
  * Dedupe is in-memory per session; ``--dedupe-from-trades-log``
    optionally pre-seeds the dedupe table from the last 100 rows of
    ``logs/trades.jsonl`` so a loop restart does not immediately
    re-fire an identical mock trade.

The quote feed (``tools.quote_feed``) is the data side and runs in its
own process per the Phase-5.5 contract; this loop only consumes
``logs/quotes.jsonl`` via tail-read.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vision.chart_calibration import CalibrationError, ChartCalibration
from vision.fibo_line_detector import detect_fibo_lines
from vision.fibo_line_filter import FilterParams, filter_fibo_lines
from tools.mock_fibo_signal import generate_signal, generate_signal_via_strategy


@dataclass(frozen=True)
class WatchLoopConfig:
    offline_image: Optional[str] = None
    live_capture: bool = False
    config_path: str = "config/capture.yaml"
    quotes_file: str = "logs/quotes.jsonl"
    watch_log: str = "logs/watch_loop.jsonl"
    interval_sec: float = 5.0
    max_iterations: int = 0           # 0 = run until SIGINT
    tolerance_px: int = 8
    min_confidence: float = 0.55
    min_length_ratio: float = 0.4
    filter_top_k: int = 8
    submit: bool = False              # opt-in
    dedupe_cooldown_sec: int = 60
    dedupe_from_trades_log: bool = False
    notify_mode: str = "off"          # off | dry | live
    symbol: str = "MOCK"
    strategy: str = "touch"           # touch | mob_v2


def _refuse_if_live() -> None:
    live = (os.getenv("LIVE_TRADING", "false") or "").strip().lower()
    if live != "false":
        raise SystemExit(
            f"watch_fibo_loop refuses to start: LIVE_TRADING={live!r} (must be 'false')."
        )
    em = (os.getenv("EXECUTION_MODE", "mock") or "").strip().lower()
    if em != "mock":
        raise SystemExit(
            f"watch_fibo_loop refuses to start: EXECUTION_MODE={em!r} (must be 'mock')."
        )


def _now() -> tuple[float, str]:
    return time.time(), datetime.now(timezone.utc).isoformat()


def _seg_to_dict(seg) -> dict:
    return {
        "y": seg.y,
        "x_start": seg.x_start,
        "x_end": seg.x_end,
        "length": seg.length,
        "color_bgr": list(seg.color_bgr),
        "color_rgb": list(seg.color_rgb),
        "color_hex": seg.color_hex,
        "angle_deg": float(seg.angle_deg),
        "confidence": float(seg.confidence),
    }


class WatchLoopRunner:
    def __init__(self, cfg: WatchLoopConfig) -> None:
        self.cfg = cfg
        self._last_fired: dict[str, float] = {}
        self._prev_signature: Optional[str] = None
        self._validate()
        if cfg.dedupe_from_trades_log:
            self._preload_dedupe_from_trades_log()

    def _validate(self) -> None:
        if not self.cfg.offline_image and not self.cfg.live_capture:
            raise ValueError(
                "watch_fibo_loop: either --offline-image or --live-capture must be set"
            )
        if self.cfg.offline_image and self.cfg.live_capture:
            raise ValueError(
                "watch_fibo_loop: --offline-image and --live-capture are mutually exclusive"
            )
        if self.cfg.notify_mode not in ("off", "dry", "live"):
            raise ValueError(f"notify_mode must be off|dry|live, got {self.cfg.notify_mode!r}")

    # -- capture --------------------------------------------------------------

    def _load_frame(self, record: dict):
        if self.cfg.offline_image:
            record["capture"] = {"source": f"offline:{self.cfg.offline_image}"}
            path = Path(self.cfg.offline_image)
            if not path.exists():
                record["capture"]["error"] = f"file not found: {path}"
                return None
            import cv2  # type: ignore
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                record["capture"]["error"] = f"cv2 could not decode {path}"
                return None
            record["capture"]["shape"] = list(img.shape)
            return img

        # --- live capture (lazy: mss is only loaded here) ---
        record["capture"] = {"source": "live"}
        try:
            import yaml  # type: ignore
            from capture.screen_capture import CaptureRegion, ScreenCapture
            cfg_dict: dict = {}
            cp = Path(self.cfg.config_path)
            if cp.exists():
                with cp.open("r", encoding="utf-8") as f:
                    cfg_dict = yaml.safe_load(f) or {}
            region_cfg = (cfg_dict.get("region") or {}) if isinstance(cfg_dict, dict) else {}
            region = CaptureRegion(
                monitor_index=int(region_cfg.get("monitor_index", 1)),
                left=region_cfg.get("left"),
                top=region_cfg.get("top"),
                width=region_cfg.get("width"),
                height=region_cfg.get("height"),
            )
            sc = ScreenCapture(region=region)
            try:
                img = sc.grab()
            finally:
                sc.close()
            record["capture"]["shape"] = list(img.shape)
            return img
        except Exception as exc:  # capture must never crash the loop
            record["capture"]["error"] = f"{type(exc).__name__}: {exc}"
            return None

    # -- detect + filter ------------------------------------------------------

    def _detect_and_filter(self, frame, record: dict) -> list[dict]:
        result = detect_fibo_lines(frame, min_length_ratio=self.cfg.min_length_ratio)
        raw_dicts = [_seg_to_dict(s) for s in result.lines]
        record["detect"] = {
            "raw_count": len(raw_dicts),
            "min_length_ratio": self.cfg.min_length_ratio,
        }
        params = FilterParams(top_k=self.cfg.filter_top_k)
        fr = filter_fibo_lines(raw_dicts, result.image_shape, params)
        kept = [L.to_dict() for L in fr.kept]
        if kept:
            top = kept[0]
            record["filter"] = {
                "kept_count": len(kept),
                "top_color": top["color_hex"],
                "top_y": top["y"],
                "top_score": top["score"],
            }
        else:
            top_reason = fr.rejected[0].reason.split(":")[0] if fr.rejected else "no_candidates"
            record["filter"] = {
                "kept_count": 0,
                "top_color": None,
                "top_y": None,
                "top_score": None,
                "top_reject_reason": top_reason,
            }
        return kept

    # -- quote tail -----------------------------------------------------------

    def _read_latest_quote(self, record: dict) -> Optional[dict]:
        path = Path(self.cfg.quotes_file)
        if not path.exists():
            record["quote"] = {"status": "file_missing", "path": str(path)}
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            record["quote"] = {"status": "read_error", "error": f"{type(exc).__name__}: {exc}"}
            return None
        rows = [r for r in text.splitlines() if r.strip()]
        if not rows:
            record["quote"] = {"status": "empty", "path": str(path)}
            return None
        try:
            q = json.loads(rows[-1])
        except json.JSONDecodeError as exc:
            record["quote"] = {"status": "malformed", "error": str(exc)}
            return None
        if not isinstance(q, dict) or "last" not in q:
            record["quote"] = {"status": "no_last_field"}
            return None
        record["quote"] = {
            "status": "ok",
            "symbol": q.get("symbol"),
            "last": float(q["last"]),
            "ts": q.get("ts"),
        }
        return q

    # -- calibration ----------------------------------------------------------

    def _resolve_pixel_y(self, last_price: float, record: dict) -> Optional[float]:
        cp = Path(self.cfg.config_path)
        if not cp.exists():
            record["calibration"] = {"status": "missing_config", "path": str(cp)}
            return float(last_price)  # fallback: raw price-as-pixel-y
        try:
            cal = ChartCalibration.from_yaml(cp)
        except CalibrationError as exc:
            record["calibration"] = {"status": "invalid", "error": str(exc)}
            return None
        if cal is None:
            record["calibration"] = {"status": "not_set"}
            return float(last_price)
        pixel_y = cal.price_to_pixel_y(last_price)
        record["calibration"] = {"status": "ok", "pixel_y": round(pixel_y, 3)}
        return pixel_y

    # -- signal ---------------------------------------------------------------

    def _build_signal(
        self,
        kept: list[dict],
        pixel_y: Optional[float],
        record: dict,
    ) -> dict:
        cal_status = record.get("calibration", {}).get("status")
        quote_status = record.get("quote", {}).get("status")

        side = "FLAT"
        fibo_y: Optional[int] = None
        conf = 0.0
        if quote_status != "ok":
            reason = f"no price available ({quote_status})"
        elif cal_status == "invalid":
            err = record["calibration"].get("error", "unknown")
            reason = f"calibration invalid: {err}"
        elif not kept:
            reason = "no fibo lines kept after filter"
        elif pixel_y is None:
            reason = "no pixel_y available"
        else:
            if self.cfg.strategy == "mob_v2":
                image_shape = (0, 0)
                cap_shape = record.get("capture", {}).get("shape")
                if isinstance(cap_shape, (list, tuple)) and len(cap_shape) >= 2:
                    image_shape = (int(cap_shape[0]), int(cap_shape[1]))
                sig = generate_signal_via_strategy(
                    fibo_lines=kept,
                    image_shape=image_shape,
                    price_y=pixel_y,
                    symbol=self.cfg.symbol,
                    min_confidence=self.cfg.min_confidence,
                )
            else:
                sig = generate_signal(
                    fibo_lines=kept,
                    price_y=pixel_y,
                    symbol=self.cfg.symbol,
                    tolerance_px=self.cfg.tolerance_px,
                    min_confidence=self.cfg.min_confidence,
                )
            side = sig["side"]
            fibo_y = sig["fibo_line_y"]
            conf = float(sig["confidence"])
            reason = sig["reason"]

        record["signal"] = {
            "side": side,
            "fibo_line_y": fibo_y,
            "confidence": conf,
            "reason": reason,
        }
        return {
            "side": side,
            "fibo_line_y": fibo_y,
            "confidence": conf,
            "reason": reason,
            "symbol": self.cfg.symbol,
        }

    # -- dedupe ---------------------------------------------------------------

    def _dedupe_key(self, sig: dict) -> str:
        return f"{sig['side']}:{sig['fibo_line_y']}"

    def _check_dedupe(self, sig: dict, record: dict) -> bool:
        if sig["side"] == "FLAT":
            record["dedupe"] = {"key": None, "action": "n/a_flat"}
            return False
        key = self._dedupe_key(sig)
        now = time.time()
        last_ts = self._last_fired.get(key)
        if last_ts is not None and (now - last_ts) < self.cfg.dedupe_cooldown_sec:
            record["dedupe"] = {
                "key": key,
                "action": "suppressed_same_key",
                "age_sec": round(now - last_ts, 2),
            }
            return False
        record["dedupe"] = {"key": key, "action": "fired"}
        self._last_fired[key] = now
        return True

    def _preload_dedupe_from_trades_log(self) -> None:
        """Seed dedupe state from recent mock fills.

        Only fills whose timestamp is still within the cooldown window
        carry forward; older fills are ignored, so a loop restart hours
        after the last trade does NOT incorrectly suppress a fresh signal.
        """
        log = Path(os.getenv("TRADES_LOG", "logs/trades.jsonl"))
        if not log.exists():
            return
        try:
            text = log.read_text(encoding="utf-8")
        except OSError:
            return
        now = time.time()
        cutoff = now - self.cfg.dedupe_cooldown_sec
        rows = [r for r in text.splitlines() if r.strip()][-100:]
        for row in rows:
            try:
                fill = json.loads(row)
            except json.JSONDecodeError:
                continue
            if not isinstance(fill, dict) or fill.get("mode") != "mock":
                continue
            side = fill.get("side")
            entry = fill.get("entry")
            if side is None or entry is None:
                continue
            ts = float(fill.get("ts", 0.0))
            if ts < cutoff:
                continue
            key = f"{side}:{int(round(float(entry)))}"
            self._last_fired[key] = ts

    # -- submit ---------------------------------------------------------------

    def _maybe_submit(
        self,
        sig: dict,
        pixel_y: Optional[float],
        record: dict,
    ) -> None:
        if not self.cfg.submit:
            record["execution"] = {
                "mode": "skipped",
                "submitted": False,
                "reason": "--submit not set",
            }
            return
        if sig["side"] == "FLAT":
            record["execution"] = {
                "mode": "skipped",
                "submitted": False,
                "reason": "FLAT signal",
            }
            return
        if record["dedupe"]["action"] != "fired":
            record["execution"] = {
                "mode": "skipped",
                "submitted": False,
                "reason": "dedupe suppressed",
            }
            return

        # Lazy imports keep the module-top clean (guarded by test).
        try:
            from executor.mock_executor import MockExecutor
            from risk.risk_gate import RiskGate
            from strategy.fibo_mob_v2 import Signal as StrategySignal
        except Exception as exc:
            record["execution"] = {
                "mode": "import_error",
                "submitted": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return

        entry = float(pixel_y if pixel_y is not None else 0.0)
        strategy_sig = StrategySignal(
            side=sig["side"],
            entry=entry,
            stop=entry + 20.0,
            target=entry - 40.0,
            confidence=float(sig["confidence"]),
            reason=sig["reason"],
        )
        try:
            gate = RiskGate(min_confidence=float(sig["confidence"]))
            decision = gate.check(strategy_sig)
        except Exception as exc:
            record["execution"] = {
                "mode": "risk_error",
                "submitted": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return
        if not decision.approved:
            record["execution"] = {
                "mode": "rejected_by_risk",
                "submitted": False,
                "reason": decision.reason,
            }
            return
        try:
            fill = MockExecutor().submit(strategy_sig)
        except Exception as exc:
            record["execution"] = {
                "mode": "executor_error",
                "submitted": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return
        record["execution"] = {
            "mode": fill.mode,
            "submitted": True,
            "side": fill.side,
            "entry": fill.entry,
            "stop": fill.stop,
            "target": fill.target,
        }

    # -- notify ---------------------------------------------------------------

    def _maybe_notify(self, sig: dict, record: dict) -> None:
        if self.cfg.notify_mode == "off":
            record["notify"] = {"mode": "off", "sent": False}
            return

        signature = f"{sig['side']}:{sig['fibo_line_y']}"
        if signature == self._prev_signature:
            record["notify"] = {
                "mode": self.cfg.notify_mode,
                "sent": False,
                "reason": "no state transition",
            }
            return
        self._prev_signature = signature

        if sig["side"] == "FLAT":
            record["notify"] = {
                "mode": self.cfg.notify_mode,
                "sent": False,
                "reason": "transition into FLAT - not notifying",
            }
            return

        try:
            from notify.telegram_bot import TelegramBot
        except Exception as exc:
            record["notify"] = {
                "mode": self.cfg.notify_mode,
                "sent": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return

        text = (
            f"[MOCK {sig['side']}] symbol={sig['symbol']} y={sig['fibo_line_y']} "
            f"conf={sig['confidence']:.2f} reason={sig['reason']}"
        )
        try:
            bot = TelegramBot(dry_run=(self.cfg.notify_mode == "dry"))
            result = bot.send(text)
        except Exception as exc:
            record["notify"] = {
                "mode": self.cfg.notify_mode,
                "sent": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            return
        record["notify"] = {
            "mode": self.cfg.notify_mode,
            "sent": bool(result.get("ok")),
            "dry_run": bool(result.get("dry_run", False)),
            "skipped": bool(result.get("skipped", False)),
        }

    # -- one iteration --------------------------------------------------------

    def _stub_remaining(self, record: dict, *, reason: str) -> None:
        record["detect"] = {"raw_count": 0, "min_length_ratio": self.cfg.min_length_ratio}
        record["filter"] = {
            "kept_count": 0,
            "top_color": None,
            "top_y": None,
            "top_score": None,
        }
        record["quote"] = {"status": "skipped_no_frame"}
        record["calibration"] = {"status": "skipped_no_frame"}
        record["signal"] = {
            "side": "FLAT",
            "fibo_line_y": None,
            "confidence": 0.0,
            "reason": reason,
        }
        record["dedupe"] = {"key": None, "action": "n/a_no_frame"}
        record["execution"] = {"mode": "skipped", "submitted": False, "reason": reason}
        record["notify"] = {"mode": self.cfg.notify_mode, "sent": False, "reason": reason}

    def run_one_iteration(self, iter_idx: int) -> dict:
        ts, iso = _now()
        record: dict = {"iter": iter_idx, "ts": ts, "timestamp": iso}

        frame = self._load_frame(record)
        if frame is None:
            self._stub_remaining(record, reason="no frame")
            return record

        kept = self._detect_and_filter(frame, record)
        quote = self._read_latest_quote(record)

        pixel_y: Optional[float] = None
        if quote is not None:
            pixel_y = self._resolve_pixel_y(float(quote["last"]), record)
        else:
            record["calibration"] = {"status": "skipped_no_quote"}

        sig = self._build_signal(kept, pixel_y, record)
        self._check_dedupe(sig, record)
        self._maybe_submit(sig, pixel_y, record)
        self._maybe_notify(sig, record)
        return record

    # -- main loop ------------------------------------------------------------

    def run(self) -> int:
        log = Path(self.cfg.watch_log)
        log.parent.mkdir(parents=True, exist_ok=True)
        i = 0
        try:
            while True:
                i += 1
                rec = self.run_one_iteration(i)
                with log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                sig = rec.get("signal", {})
                exec_info = rec.get("execution", {})
                print(
                    f"[loop] #{i} signal={sig.get('side')} "
                    f"y={sig.get('fibo_line_y')} conf={sig.get('confidence', 0):.2f} "
                    f"submitted={exec_info.get('submitted', False)} "
                    f"dedupe={rec.get('dedupe', {}).get('action')}"
                )
                if self.cfg.max_iterations and i >= self.cfg.max_iterations:
                    break
                if self.cfg.interval_sec > 0:
                    time.sleep(self.cfg.interval_sec)
        except KeyboardInterrupt:
            print(f"[loop] interrupted after {i} iterations")
        return 0


def _parse_args(argv: Optional[list[str]] = None) -> WatchLoopConfig:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--offline-image", default=None,
                     help="path to a PNG to use as the frame each iteration (no mss)")
    src.add_argument("--live-capture", action="store_true",
                     help="capture from the screen via mss (requires a display)")
    ap.add_argument("--config", default="config/capture.yaml", dest="config_path",
                    help="YAML: capture region + chart calibration")
    ap.add_argument("--quotes-file", default="logs/quotes.jsonl")
    ap.add_argument("--watch-log",   default="logs/watch_loop.jsonl")
    ap.add_argument("--interval",    type=float, default=5.0, dest="interval_sec")
    ap.add_argument("--max-iterations", type=int, default=0)
    ap.add_argument("--tolerance-px",   type=int,   default=8)
    ap.add_argument("--min-confidence", type=float, default=0.55)
    ap.add_argument("--min-length-ratio", type=float, default=0.4)
    ap.add_argument("--filter-top-k",   type=int,   default=8)
    ap.add_argument("--submit", action="store_true",
                    help="opt-in: pipe non-FLAT signals through RiskGate + MockExecutor")
    ap.add_argument("--dedupe-cooldown-sec", type=int, default=60)
    ap.add_argument("--dedupe-from-trades-log", action="store_true")
    ap.add_argument("--notify-mode", choices=("off", "dry", "live"), default="off")
    ap.add_argument("--symbol", default="MOCK")
    ap.add_argument("--strategy", choices=("touch", "mob_v2"), default="touch",
                    help="touch: Phase-5 nearest-line LONG/FLAT (default). "
                         "mob_v2: Phase-6 wiring through vision.fibo_line_level_namer "
                         "+ strategy.fibo_mob_v2.FiboMobV2.evaluate().")
    ns = ap.parse_args(argv)
    return WatchLoopConfig(
        offline_image=ns.offline_image,
        live_capture=ns.live_capture,
        config_path=ns.config_path,
        quotes_file=ns.quotes_file,
        watch_log=ns.watch_log,
        interval_sec=ns.interval_sec,
        max_iterations=ns.max_iterations,
        tolerance_px=ns.tolerance_px,
        min_confidence=ns.min_confidence,
        min_length_ratio=ns.min_length_ratio,
        filter_top_k=ns.filter_top_k,
        submit=ns.submit,
        dedupe_cooldown_sec=ns.dedupe_cooldown_sec,
        dedupe_from_trades_log=ns.dedupe_from_trades_log,
        notify_mode=ns.notify_mode,
        symbol=ns.symbol,
        strategy=ns.strategy,
    )


def main(argv: Optional[list[str]] = None) -> int:
    _refuse_if_live()
    cfg = _parse_args(argv)
    runner = WatchLoopRunner(cfg)
    return runner.run()


if __name__ == "__main__":
    sys.exit(main())
