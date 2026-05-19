"""Generate mock trading signals from detected Fibo lines + a price source.

Reads ``logs/fibo_lines.json`` (produced by ``tools.detect_fibo_lines``),
takes a price expressed in image y-pixel space (same coordinate
system as the lines), and emits a JSON signal record. If
``--submit`` is set and the signal is not FLAT, the signal is also
piped through ``risk.risk_gate.RiskGate`` and ``executor.mock_executor.MockExecutor``
so the trade log mirrors what the live pipeline would record.

Two price sources, selected by ``--price-source``:

  * ``mock_y`` (default): use ``--price-y`` directly. Keeps the
    Phase-5 unit tests deterministic.
  * ``latest_quote``: tail the last record from ``--quotes-file``
    (default ``logs/quotes.jsonl``, written by ``tools.quote_feed``
    in its own process), take its ``last`` field as the y-pixel
    price. If the file is missing or empty, emit FLAT. This is the
    Phase-5.5 wiring step; price-to-pixel calibration is a follow-up.

Strictly mock-only:
  * imports only stdlib + this repo's modules; no network library;
  * never reads or sends a broker credential;
  * never imports anything from ``quote.*`` -- the file-based JSONL
    contract is the only IPC channel, so the quote feed runs in its
    own process and this side cannot accidentally spawn one in-process;
  * MockExecutor's own boundary check refuses to write to
    ``logs/trades.jsonl`` unless ``LIVE_TRADING=false`` and
    ``EXECUTION_MODE=mock``.

Run:
    python -m tools.mock_fibo_signal --price-y 450 --symbol XAUUSD
    python -m tools.mock_fibo_signal --price-source latest_quote --symbol XAUUSD --submit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_FIBO_PATH = "logs/fibo_lines.json"
DEFAULT_SIGNALS_PATH = "logs/signals.jsonl"
DEFAULT_QUOTES_PATH = "logs/quotes.jsonl"
DEFAULT_CALIBRATION_PATH = "config/capture.yaml"


def _now() -> tuple[float, str]:
    ts = time.time()
    iso = datetime.now(timezone.utc).isoformat()
    return ts, iso


def _signal_record(
    *,
    symbol: str,
    side: str,
    reason: str,
    fibo_line_y: Optional[int],
    confidence: float,
) -> dict:
    ts, iso = _now()
    return {
        "ts": ts,
        "timestamp": iso,
        "symbol": symbol,
        "side": side,
        "reason": reason,
        "fibo_line_y": fibo_line_y,
        "confidence": float(confidence),
        "mode": "mock",
    }


def _nearest_line(lines: list[dict], price_y: float, tolerance_px: int):
    if not lines:
        return None, None
    nearest = min(lines, key=lambda line: abs(line["y"] - price_y))
    distance = abs(nearest["y"] - price_y)
    if distance <= tolerance_px:
        return nearest, distance
    return None, distance


def generate_signal(
    *,
    fibo_lines: list[dict],
    price_y: float,
    symbol: str,
    tolerance_px: int,
    min_confidence: float,
) -> dict:
    """Pure function: lines + price -> signal dict. No I/O, no networking."""
    hit, dist = _nearest_line(fibo_lines, price_y, tolerance_px)
    if hit is None:
        return _signal_record(
            symbol=symbol,
            side="FLAT",
            reason=f"no fibo line within {tolerance_px}px of price_y={price_y}",
            fibo_line_y=None,
            confidence=0.0,
        )

    conf = float(hit.get("confidence", 0.0))
    if conf < min_confidence:
        return _signal_record(
            symbol=symbol,
            side="FLAT",
            reason=f"fibo touch at y={hit['y']} but confidence {conf:.2f} < {min_confidence}",
            fibo_line_y=int(hit["y"]),
            confidence=conf,
        )

    # MVP convention: a clean Fibo touch is a LONG bounce. The point of
    # this layer is to wire the plumbing, not to backtest a thesis.
    return _signal_record(
        symbol=symbol,
        side="LONG",
        reason=f"fibo touch y={hit['y']} dist={dist}px",
        fibo_line_y=int(hit["y"]),
        confidence=conf,
    )


def _append_signal(signal: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(signal, ensure_ascii=False) + "\n")


def generate_signal_via_strategy(
    *,
    fibo_lines: list[dict],
    image_shape: tuple[int, int],
    price_y: float,
    symbol: str,
    min_confidence: float,
) -> dict:
    """Phase-6 path: namer + FiboMobV2.evaluate() instead of nearest-touch.

    Bridges the CV-detected ``FiboLineSegment`` list to a
    ``FiboDetection`` with named levels (via
    ``vision.fibo_line_level_namer.name_levels``), then routes the
    decision through ``strategy.fibo_mob_v2.FiboMobV2``. Pure function;
    no I/O. The Phase-5 ``generate_signal()`` above is preserved as a
    second strategy choice and remains the default for backward compat.
    """
    from vision.fibo_line_level_namer import name_levels
    from strategy.fibo_mob_v2 import FiboMobV2

    detection = name_levels(fibo_lines, image_shape=image_shape)
    strat = FiboMobV2(min_confidence=min_confidence)
    decision = strat.evaluate(detection, last_price=price_y)

    nearest_y: Optional[int] = None
    if detection.ok and detection.levels:
        nearest = min(detection.levels, key=lambda lv: abs(lv.y_pixel - price_y))
        nearest_y = int(nearest.y_pixel)

    return _signal_record(
        symbol=symbol,
        side=decision.side,
        reason=f"fibo-mob-v2: {decision.reason}",
        fibo_line_y=nearest_y,
        confidence=float(decision.confidence),
    )


def _submit_through_executor(signal: dict, price_y: float) -> Optional[dict]:
    """Pipe an approved non-FLAT signal through RiskGate + MockExecutor.

    Returns the fill dict if the executor wrote a row, otherwise None.
    """
    if signal["side"] == "FLAT":
        return None

    from risk.risk_gate import RiskGate
    from executor.mock_executor import MockExecutor
    from strategy.fibo_mob_v2 import Signal as StrategySignal

    gate = RiskGate(min_confidence=signal["confidence"])

    strategy_sig = StrategySignal(
        side=signal["side"],
        entry=float(price_y),
        stop=float(price_y) + 20.0,
        target=float(price_y) - 40.0,
        confidence=float(signal["confidence"]),
        reason=signal["reason"],
    )
    decision = gate.check(strategy_sig)
    if not decision.approved:
        return {"submitted": False, "reason": decision.reason}

    executor = MockExecutor()
    fill = executor.submit(strategy_sig)
    return {
        "submitted": True,
        "mode": fill.mode,
        "side": fill.side,
        "entry": fill.entry,
        "stop": fill.stop,
        "target": fill.target,
    }


def _synthesize_auto_approval(operator_chat_id: str):
    """Build an auto-approved ApprovalDecision without touching the gate.

    Used by ``--route --approval-mode=none`` so the operator can skip
    the ApprovalGate dry-run entirely while still routing through
    ExecutorRouter (so EXECUTION_MODE=paper still works)."""
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    from approval.models import ApprovalDecision

    ts = _time.time()
    iso = _dt.now(_tz.utc).isoformat()
    return ApprovalDecision(
        request_id=f"cli-auto-{int(ts * 1000)}",
        approved=True,
        reason="auto_approved",
        operator_chat_id=str(operator_chat_id),
        decided_ts=ts,
        decided_timestamp=iso,
        elapsed_ms=0.0,
    )


def _route_through_pipeline(
    *,
    signal_record: dict,
    price_y: float,
    operator_chat_id: str,
    approval_mode: str,
    approval_timeout: float,
    approvals_log: Optional[str],
    router_log: Optional[str],
    execution_mode: Optional[str] = None,
) -> dict:
    """Pipe a non-FLAT signal through RiskGate -> ApprovalGate ->
    ExecutorRouter. Returns a summary dict.

    Notes:
      * FLAT signals are rejected up-front (no pipeline entry).
      * ApprovalGate.timeout_seconds is taken from ``approval_timeout``.
      * Router lazy-constructs so EXECUTION_MODE env is read fresh.
      * If any gate refuses construction (the kill-switch envelope
        is not set to false), this function catches it and returns
        ``routed=False`` with an ``init_failure`` reason; the CLI
        prints the reason and exits zero (no fill row appears).
    """
    if signal_record["side"] == "FLAT":
        return {"routed": False, "reason": "FLAT signal is not eligible for routing"}

    import threading
    import time as _time

    from approval.approval_gate import (
        ApprovalGate,
        LiveTradingForbidden as ApprovalLiveTradingForbidden,
    )
    from executor.executor_router import (
        ExecutorRouter,
        LiveTradingForbidden as RouterLiveTradingForbidden,
    )
    from risk.risk_gate import (
        RiskGate,
        LiveTradingForbidden as RiskLiveTradingForbidden,
    )
    from strategy.fibo_mob_v2 import Signal as StrategySignal

    sig = StrategySignal(
        side=signal_record["side"],
        entry=float(price_y),
        stop=float(price_y) + 20.0,
        target=float(price_y) - 40.0,
        confidence=float(signal_record["confidence"]),
        reason=signal_record["reason"],
    )

    try:
        risk = RiskGate(min_confidence=float(signal_record["confidence"]))
    except RiskLiveTradingForbidden as e:
        return {"routed": False, "reason": f"init_failure: risk_gate {e}"}

    risk_decision = risk.check(sig)
    if not risk_decision.approved:
        return {
            "routed": False,
            "reason": f"risk_gate rejected: {risk_decision.reason}",
        }

    if approval_mode == "none":
        approval = _synthesize_auto_approval(operator_chat_id)
    else:
        try:
            gate = ApprovalGate(
                timeout_seconds=approval_timeout,
                allowed_chat_ids=[str(operator_chat_id)],
                log_path=approvals_log,
            )
        except ApprovalLiveTradingForbidden as e:
            return {"routed": False, "reason": f"init_failure: approval_gate {e}"}

        req = gate.request(
            symbol=signal_record["symbol"],
            side=sig.side,
            entry=sig.entry,
            stop=sig.stop,
            target=sig.target,
            confidence=sig.confidence,
            reason=sig.reason,
        )

        if approval_mode in ("auto-approve", "auto-reject"):
            approve_flag = (approval_mode == "auto-approve")

            def _submit() -> None:
                _time.sleep(0.01)
                gate.submit_decision(
                    req.request_id,
                    approve=approve_flag,
                    operator_chat_id=str(operator_chat_id),
                )

            threading.Thread(target=_submit, daemon=True).start()
        # "timeout" -> do not submit; await_decision will record timeout.
        approval = gate.await_decision(req)

    try:
        router = ExecutorRouter(
            execution_mode=execution_mode,
            log_path=router_log,
        )
    except RouterLiveTradingForbidden as e:
        return {"routed": False, "reason": f"init_failure: router {e}"}

    outcome = router.route(sig, approval, symbol=signal_record["symbol"])
    return {
        "routed": True,
        "submitted": outcome.submitted,
        "adapter": outcome.adapter,
        "execution_mode": outcome.execution_mode,
        "skip_reason": outcome.skip_reason,
        "approval_reason": approval.reason,
        "approval_request_id": approval.request_id,
    }


def _notify_telegram(signal: dict, fill: Optional[dict], suppressed: bool) -> Optional[dict]:
    """Dry-run-safe Telegram notification after submit. Never raises.

    Default behaviour:
      * if ``suppressed`` is True (operator passed ``--no-telegram``): skip.
      * if the signal is FLAT or the fill was rejected: skip.
      * else call ``notify.telegram_bot.TelegramBot().send(...)``.
        - If ``TELEGRAM_DRY_RUN=1`` is set, the bot prints
          "would send" and returns ``dry_run=True`` without networking.
        - If credentials are absent, the bot returns ``skipped=True``.
        - If credentials are present and dry-run is off, the bot
          posts to Telegram (the operator's decision, not ours).

    Returns the bot's response dict, or None when skipped.
    """
    if suppressed:
        return None
    if signal["side"] == "FLAT":
        return None
    if fill is None or not fill.get("submitted"):
        return None

    try:
        from notify.telegram_bot import TelegramBot
        text = (
            f"[mock] {signal['symbol']} {signal['side']} "
            f"conf={signal['confidence']:.2f} entry={fill.get('entry')} "
            f"stop={fill.get('stop')} target={fill.get('target')} "
            f"reason={signal['reason']}"
        )
        return TelegramBot().send(text)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fibo-lines", default=DEFAULT_FIBO_PATH)
    ap.add_argument("--out-signals", default=DEFAULT_SIGNALS_PATH)
    ap.add_argument("--symbol", default="MOCK")
    ap.add_argument(
        "--price-y",
        type=float,
        default=None,
        help="price expressed in image y-pixel space; required when --price-source=mock_y",
    )
    ap.add_argument(
        "--price-source",
        default="mock_y",
        choices=("mock_y", "latest_quote"),
        help="mock_y: use --price-y directly. latest_quote: tail --quotes-file.",
    )
    ap.add_argument("--quotes-file", default=DEFAULT_QUOTES_PATH,
                    help="JSONL file produced by tools.quote_feed (latest_quote mode only)")
    ap.add_argument("--calibration-config", default=DEFAULT_CALIBRATION_PATH,
                    help="YAML file with a top-level 'calibration:' block "
                         "(latest_quote mode only). If absent or no block, falls back "
                         "to raw price-as-pixel-y interpretation.")
    ap.add_argument("--tolerance-px", type=int, default=8)
    ap.add_argument("--min-confidence", type=float, default=0.55)
    ap.add_argument("--json", action="store_true", help="emit JSON to stdout instead of summary text")
    ap.add_argument(
        "--submit",
        action="store_true",
        help="pipe an approved non-FLAT signal through RiskGate + MockExecutor "
             "so a row lands in logs/trades.jsonl",
    )
    ap.add_argument(
        "--strategy",
        default="touch",
        choices=("touch", "mob_v2"),
        help="touch: Phase-5 nearest-line LONG/FLAT (default; backward compat). "
             "mob_v2: Phase-6 wiring through vision.fibo_line_level_namer + "
             "strategy.fibo_mob_v2.FiboMobV2.evaluate().",
    )
    ap.add_argument(
        "--no-telegram",
        action="store_true",
        help="suppress the post-submit Telegram notification. By default a "
             "notification is built but the bot stays in dry-run unless "
             "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set in env.",
    )
    ap.add_argument(
        "--route",
        action="store_true",
        help="route the signal through RiskGate -> ApprovalGate -> "
             "ExecutorRouter so EXECUTION_MODE=paper actually reaches "
             "PaperExecutor. Mutually exclusive with --submit (legacy "
             "MockExecutor-direct path).",
    )
    ap.add_argument(
        "--approval-mode",
        default="none",
        choices=("auto-approve", "auto-reject", "timeout", "none"),
        help="how ApprovalGate is exercised under --route. "
             "auto-approve/auto-reject: spawn a worker thread that submits "
             "a decision after a tiny delay. timeout: let ApprovalGate "
             "fire its own timeout decision. none (default): skip "
             "ApprovalGate entirely and synthesize an auto_approved "
             "decision for the router.",
    )
    ap.add_argument(
        "--operator-chat-id",
        default="12345",
        help="simulated operator chat id for ApprovalGate's allowlist "
             "and the submit_decision callback under --route.",
    )
    ap.add_argument(
        "--approval-timeout",
        type=float,
        default=5.0,
        help="ApprovalGate timeout in seconds under --route. Tests may "
             "set this very small (e.g. 0.1) to exercise the timeout "
             "branch quickly.",
    )
    ap.add_argument(
        "--approvals-log",
        default=None,
        help="ApprovalGate log path override (default: env APPROVALS_LOG "
             "or logs/approvals.jsonl).",
    )
    ap.add_argument(
        "--router-log",
        default=None,
        help="ExecutorRouter log path override (default: env "
             "ROUTER_DECISIONS_LOG or logs/router_decisions.jsonl).",
    )
    ap.add_argument(
        "--execution-mode",
        default=None,
        choices=("mock", "paper"),
        help="explicit execution mode for ExecutorRouter under "
             "--route. When omitted, Router reads EXECUTION_MODE env. "
             "Note: the EXECUTION_MODE env must remain 'mock' for "
             "RiskGate's invariant; this flag lets the routed signal "
             "land in PaperExecutor without violating that.",
    )
    args = ap.parse_args()

    if args.submit and args.route:
        print(
            "ERROR: --submit and --route are mutually exclusive. "
            "--submit goes directly to MockExecutor (legacy); --route "
            "goes through RiskGate -> ApprovalGate -> ExecutorRouter.",
            file=sys.stderr,
        )
        return 2

    fibo_path = Path(args.fibo_lines)
    if not fibo_path.exists():
        print(f"FAIL: fibo lines file not found: {fibo_path}", file=sys.stderr)
        return 2

    try:
        data = json.loads(fibo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"FAIL: {fibo_path} is not valid JSON: {exc}", file=sys.stderr)
        return 3

    lines = data.get("lines", []) if isinstance(data, dict) else []

    price_y, price_note = _resolve_price_y(args)
    out_signals = Path(args.out_signals)

    if price_y is None:
        # No price available (latest_quote mode with missing/empty file) ->
        # emit FLAT and exit cleanly without touching the executor.
        signal = _signal_record(
            symbol=args.symbol,
            side="FLAT",
            reason=f"no price available ({price_note})",
            fibo_line_y=None,
            confidence=0.0,
        )
        _append_signal(signal, out_signals)
        if args.json:
            print(json.dumps({"signal": signal, "fill": None, "price_source": price_note}, ensure_ascii=False))
        else:
            print(f"signal: FLAT   symbol={args.symbol}  (no price)")
            print(f"reason: {signal['reason']}")
            print(f"wrote   -> {out_signals}")
        return 0

    if args.strategy == "mob_v2":
        image_shape = (0, 0)
        if isinstance(data, dict):
            raw_shape = data.get("image_shape")
            if isinstance(raw_shape, (list, tuple)) and len(raw_shape) >= 2:
                image_shape = (int(raw_shape[0]), int(raw_shape[1]))
        signal = generate_signal_via_strategy(
            fibo_lines=lines,
            image_shape=image_shape,
            price_y=price_y,
            symbol=args.symbol,
            min_confidence=args.min_confidence,
        )
    else:
        signal = generate_signal(
            fibo_lines=lines,
            price_y=price_y,
            symbol=args.symbol,
            tolerance_px=args.tolerance_px,
            min_confidence=args.min_confidence,
        )

    _append_signal(signal, out_signals)

    fill = None
    notify = None
    route_summary: Optional[dict] = None
    if args.submit:
        fill = _submit_through_executor(signal, price_y)
        notify = _notify_telegram(signal, fill, suppressed=args.no_telegram)
    elif args.route:
        route_summary = _route_through_pipeline(
            signal_record=signal,
            price_y=price_y,
            operator_chat_id=args.operator_chat_id,
            approval_mode=args.approval_mode,
            approval_timeout=args.approval_timeout,
            approvals_log=args.approvals_log,
            router_log=args.router_log,
            execution_mode=args.execution_mode,
        )

    if args.json:
        print(json.dumps(
            {"signal": signal, "fill": fill, "notify": notify,
             "route": route_summary,
             "strategy": args.strategy, "price_source": price_note},
            ensure_ascii=False,
        ))
    else:
        print(
            f"signal: {signal['side']:5}  symbol={signal['symbol']}  "
            f"fibo_y={signal['fibo_line_y']}  conf={signal['confidence']:.2f}  "
            f"price_y={price_y}  strategy={args.strategy}"
        )
        print(f"price : {price_note}")
        print(f"reason: {signal['reason']}")
        print(f"wrote   -> {out_signals}")
        if fill and fill.get("submitted"):
            print(f"submitted via MockExecutor -> logs/trades.jsonl (mode={fill['mode']})")
            if notify is not None:
                if notify.get("dry_run"):
                    print(f"telegram: dry-run (would send)")
                elif notify.get("skipped"):
                    print(f"telegram: skipped ({notify.get('reason', 'disabled')})")
                elif notify.get("ok"):
                    print(f"telegram: sent (status={notify.get('status')})")
                else:
                    print(f"telegram: failed ({notify.get('error', notify)})")
        elif fill is not None:
            print(f"not submitted: {fill.get('reason', 'rejected by risk gate')}")
        elif args.submit:
            print("not submitted: signal is FLAT")
        if route_summary is not None:
            if route_summary.get("routed"):
                print(
                    f"routed  via ExecutorRouter ({route_summary['execution_mode']}/"
                    f"{route_summary['adapter']}) submitted={route_summary['submitted']}"
                    + (f" skip_reason={route_summary['skip_reason']}" if route_summary.get('skip_reason') else "")
                )
                print(
                    f"approval reason={route_summary.get('approval_reason')} "
                    f"request_id={route_summary.get('approval_request_id')}"
                )
            else:
                print(f"not routed: {route_summary.get('reason')}")
    return 0


def _resolve_price_y(args) -> tuple[Optional[float], str]:
    """Return (price_y, source_note). price_y=None means "no price -> FLAT"."""
    if args.price_source == "mock_y":
        if args.price_y is None:
            print(
                "FAIL: --price-y is required when --price-source=mock_y",
                file=sys.stderr,
            )
            raise SystemExit(4)
        return float(args.price_y), f"mock_y={args.price_y}"

    if args.price_source == "latest_quote":
        path = Path(args.quotes_file)
        if not path.exists():
            return None, f"latest_quote: file {path} does not exist"
        try:
            rows = [r for r in path.read_text(encoding="utf-8").splitlines() if r.strip()]
        except OSError as exc:
            return None, f"latest_quote: cannot read {path}: {exc}"
        if not rows:
            return None, f"latest_quote: {path} is empty"
        try:
            quote = json.loads(rows[-1])
        except json.JSONDecodeError as exc:
            return None, f"latest_quote: last line not JSON: {exc}"
        if not isinstance(quote, dict) or "last" not in quote:
            return None, "latest_quote: last record missing 'last' field"

        last_price = float(quote["last"])
        cal_path = Path(args.calibration_config)
        cal_note = ""
        price_y = last_price
        if cal_path.exists():
            from vision.chart_calibration import ChartCalibration, CalibrationError
            try:
                cal = ChartCalibration.from_yaml(cal_path)
            except CalibrationError as exc:
                return None, f"latest_quote: calibration in {cal_path} is invalid: {exc}"
            if cal is not None:
                price_y = cal.price_to_pixel_y(last_price)
                cal_note = (
                    f" via calibration (price {last_price} -> pixel_y {price_y:.1f})"
                )
            else:
                cal_note = f" without calibration (no 'calibration:' block in {cal_path})"
        else:
            cal_note = f" without calibration ({cal_path} not found)"

        return price_y, (
            f"latest_quote {path}: last={last_price} "
            f"symbol={quote.get('symbol')} ts={quote.get('timestamp')}{cal_note}"
        )

    print(f"FAIL: unknown --price-source: {args.price_source}", file=sys.stderr)
    raise SystemExit(5)


if __name__ == "__main__":
    sys.exit(main())
