"""Mock-only soak verdict for the Phase-5 watch_loop_soak runs.

Reads the jsonl artifacts written by ``scripts/watch_loop_soak.ps1``
(watch_loop, quotes, optional trades, optional snapshots, optional
captured job logs), checks 6 critical invariants and 8 operational
thresholds, and writes a verdict report (one ``.log`` and one
``.json`` sibling). Exit codes: 0 PASS, 1 FAIL, 2 DEGRADED.

Pure stdlib. No broker SDK, no network library, no cv2 / numpy /
torch / ultralytics. The single load-bearing claim is **C1**: every
``trades_soak.jsonl`` row must have ``mode == 'mock'``. A FAIL
verdict on a single violation is what lets pytest and ops both audit
the same contract.

Critical invariants (any violation -> verdict FAIL, exit 1):

  C1  every trades_soak row has mode=mock
  C2  both soak jobs completed (passed via --jobs-ok flag)
  C3  no forbidden tokens (Traceback / Refusing / LiveTradingForbidden)
      in the merged job log
  C4  watch_loop row count within +/- 5% of duration / loop_interval
  C5  quote row count within +/- 5% of duration / quote_interval
  C6  every watch_loop row has all 12 required top-level keys

Operational warnings (any violation -> verdict DEGRADED, exit 2):

  O1  loop process working-set growth     < 50 MB
  O2  feed process working-set growth     < 30 MB
  O3  loop CPU average                    < 25% of one core
  O4  dedupe suppression ratio            >= 50%
  O5  LONG signal ratio                   >= 80%
  O6  avg watch_loop row size             < 1.5 KB
  O7  watch_loop timestamps monotonic     100%
  O8  GPU1 memory growth                  < 200 MB
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


REQUIRED_TOP_LEVEL_KEYS = [
    "iter", "ts", "timestamp", "capture", "detect", "filter",
    "quote", "calibration", "signal", "dedupe", "execution", "notify",
]

FORBIDDEN_LOG_TOKENS = [
    "Traceback (most recent call last)",
    "Refusing to",
    "LiveTradingForbidden",
]


@dataclass(frozen=True)
class SoakThresholds:
    expected_dedupe_ratio_min: float = 0.5
    expected_long_ratio_min: float = 0.8
    row_count_tolerance: float = 0.05
    avg_row_size_max_bytes: int = 1500
    loop_ws_growth_max_mb: int = 50
    feed_ws_growth_max_mb: int = 30
    loop_cpu_avg_max_pct: float = 25.0
    gpu1_growth_max_mb: int = 200


@dataclass
class SoakReport:
    verdict: str = "PASS"
    critical_violations: list[str] = field(default_factory=list)
    operational_warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_min: int = 0
    submit: bool = False

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "critical_violations": list(self.critical_violations),
            "operational_warnings": list(self.operational_warnings),
            "metrics": dict(self.metrics),
            "duration_min": self.duration_min,
            "submit": self.submit,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def to_text(self) -> str:
        lines = [
            "Phase 5 mock soak verdict",
            "=" * 50,
            f"  verdict       : {self.verdict}",
            f"  duration_min  : {self.duration_min}",
            f"  submit        : {self.submit}",
            f"  generated_at  : {datetime.now(timezone.utc).isoformat()}",
            "",
            f"Critical violations: {len(self.critical_violations)}",
        ]
        if self.critical_violations:
            for v in self.critical_violations:
                lines.append(f"  - {v}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append(f"Operational warnings: {len(self.operational_warnings)}")
        if self.operational_warnings:
            for w in self.operational_warnings:
                lines.append(f"  - {w}")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Metrics:")
        for k in sorted(self.metrics):
            lines.append(f"  {k:30s} {self.metrics[k]}")
        return "\n".join(lines) + "\n"


def _read_jsonl(path: Optional[Path]) -> list[dict]:
    if path is None or not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_parse_error": line[:200]})
    return rows


def analyze_soak(
    *,
    duration_min: int,
    loop_interval_sec: float,
    quote_interval_sec: float,
    watch_log: Path,
    quotes_log: Path,
    trades_log: Optional[Path],
    snapshots_log: Optional[Path],
    submit: bool,
    jobs_ok: bool,
    captured_logs_text: str = "",
    thresholds: Optional[SoakThresholds] = None,
) -> SoakReport:
    """Pure verdict function. No I/O writes; caller writes the report."""
    t = thresholds or SoakThresholds()
    report = SoakReport(duration_min=duration_min, submit=submit)

    watch_rows = _read_jsonl(watch_log)
    quote_rows = _read_jsonl(quotes_log)
    trades_rows = _read_jsonl(trades_log) if trades_log else []
    snapshots = _read_jsonl(snapshots_log) if snapshots_log else []

    # ---- C1: every trades row mode=mock -------------------------------------
    if submit:
        non_mock = [
            r for r in trades_rows
            if r.get("mode") != "mock" or "_parse_error" in r
        ]
        if non_mock:
            report.critical_violations.append(
                f"C1: trades_soak.jsonl has {len(non_mock)} of {len(trades_rows)} "
                f"rows with mode != 'mock' (first: {non_mock[0]})"
            )
        report.metrics["trades_count"] = len(trades_rows)
        report.metrics["trades_non_mock_count"] = len(non_mock)
    else:
        if trades_rows:
            report.critical_violations.append(
                f"C1: trades_soak.jsonl has {len(trades_rows)} row(s) but soak ran without "
                "--Submit. MockExecutor wrote despite --submit being absent."
            )
        report.metrics["trades_count"] = len(trades_rows)

    # ---- C2: both jobs completed --------------------------------------------
    if not jobs_ok:
        report.critical_violations.append(
            "C2: one or more soak jobs did not Complete (jobs_ok=False)"
        )

    # ---- C3: no forbidden tokens in captured logs --------------------------
    if captured_logs_text:
        for token in FORBIDDEN_LOG_TOKENS:
            if token in captured_logs_text:
                report.critical_violations.append(
                    f"C3: forbidden token in captured logs: {token!r}"
                )

    # ---- C4: watch_loop row count -------------------------------------------
    expected_loop = duration_min * 60 / loop_interval_sec
    actual_loop = len(watch_rows)
    lo = expected_loop * (1 - t.row_count_tolerance)
    hi = expected_loop * (1 + t.row_count_tolerance)
    report.metrics["loop_rows_expected"] = round(expected_loop, 1)
    report.metrics["loop_rows_actual"] = actual_loop
    if actual_loop < lo or actual_loop > hi:
        report.critical_violations.append(
            f"C4: watch_loop row count {actual_loop} outside expected range "
            f"[{lo:.0f}, {hi:.0f}] (expected ~{expected_loop:.0f})"
        )

    # ---- C5: quote row count ------------------------------------------------
    expected_quote = duration_min * 60 / quote_interval_sec
    actual_quote = len(quote_rows)
    qlo = expected_quote * (1 - t.row_count_tolerance)
    qhi = expected_quote * (1 + t.row_count_tolerance)
    report.metrics["quote_rows_expected"] = round(expected_quote, 1)
    report.metrics["quote_rows_actual"] = actual_quote
    if actual_quote < qlo or actual_quote > qhi:
        report.critical_violations.append(
            f"C5: quote row count {actual_quote} outside expected range "
            f"[{qlo:.0f}, {qhi:.0f}] (expected ~{expected_quote:.0f})"
        )

    # ---- C6: schema completeness --------------------------------------------
    missing_rows: list[tuple[Any, list[str]]] = []
    for r in watch_rows:
        missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in r]
        if missing:
            missing_rows.append((r.get("iter", "?"), missing))
    if missing_rows:
        first = missing_rows[0]
        report.critical_violations.append(
            f"C6: {len(missing_rows)} watch_loop row(s) missing schema keys "
            f"(first: iter={first[0]} missing={first[1]})"
        )

    # ---- O1/O2: working-set growth ------------------------------------------
    if snapshots:
        loop_ws = [s["watch_loop_ws_mb"] for s in snapshots
                   if s.get("watch_loop_ws_mb") is not None]
        feed_ws = [s["quote_feed_ws_mb"] for s in snapshots
                   if s.get("quote_feed_ws_mb") is not None]
        if loop_ws:
            growth = max(loop_ws) - min(loop_ws)
            report.metrics["loop_ws_growth_mb"] = growth
            if growth > t.loop_ws_growth_max_mb:
                report.operational_warnings.append(
                    f"O1: loop process working set grew {growth} MB "
                    f"(threshold {t.loop_ws_growth_max_mb} MB)"
                )
        if feed_ws:
            growth = max(feed_ws) - min(feed_ws)
            report.metrics["feed_ws_growth_mb"] = growth
            if growth > t.feed_ws_growth_max_mb:
                report.operational_warnings.append(
                    f"O2: feed process working set grew {growth} MB "
                    f"(threshold {t.feed_ws_growth_max_mb} MB)"
                )

    # ---- O3: loop CPU average ----------------------------------------------
    if snapshots:
        cpu_first = next(
            (s for s in snapshots if s.get("watch_loop_cpu_sec") is not None),
            None,
        )
        cpu_last = next(
            (s for s in reversed(snapshots) if s.get("watch_loop_cpu_sec") is not None),
            None,
        )
        if cpu_first is not None and cpu_last is not None and cpu_first is not cpu_last:
            cpu_delta = cpu_last["watch_loop_cpu_sec"] - cpu_first["watch_loop_cpu_sec"]
            wall_delta = cpu_last["ts"] - cpu_first["ts"]
            if wall_delta > 0:
                cpu_pct = (cpu_delta / wall_delta) * 100.0
                report.metrics["loop_cpu_avg_pct"] = round(cpu_pct, 2)
                if cpu_pct > t.loop_cpu_avg_max_pct:
                    report.operational_warnings.append(
                        f"O3: loop CPU average {cpu_pct:.1f}% > threshold "
                        f"{t.loop_cpu_avg_max_pct}% (of one core)"
                    )

    # ---- O4: dedupe suppression ratio --------------------------------------
    dedupe_actions = [r.get("dedupe", {}).get("action") for r in watch_rows]
    fired = dedupe_actions.count("fired")
    supp = dedupe_actions.count("suppressed_same_key")
    if fired + supp > 0:
        ratio = supp / (fired + supp)
        report.metrics["dedupe_ratio"] = round(ratio, 3)
        report.metrics["dedupe_fired"] = fired
        report.metrics["dedupe_suppressed"] = supp
        if ratio < t.expected_dedupe_ratio_min:
            report.operational_warnings.append(
                f"O4: dedupe ratio {ratio:.2f} below threshold "
                f"{t.expected_dedupe_ratio_min} (fired={fired}, suppressed={supp})"
            )

    # ---- O5: LONG signal ratio ---------------------------------------------
    sides = [r.get("signal", {}).get("side") for r in watch_rows]
    long_count = sides.count("LONG")
    if watch_rows:
        long_ratio = long_count / len(watch_rows)
        report.metrics["long_ratio"] = round(long_ratio, 3)
        report.metrics["signal_long"] = long_count
        report.metrics["signal_flat"] = sides.count("FLAT")
        if long_ratio < t.expected_long_ratio_min:
            report.operational_warnings.append(
                f"O5: LONG signal ratio {long_ratio:.2f} below threshold "
                f"{t.expected_long_ratio_min} ({long_count}/{len(watch_rows)})"
            )

    # ---- O6: avg row size --------------------------------------------------
    if watch_log is not None and watch_log.exists() and watch_rows:
        size_b = watch_log.stat().st_size
        avg = size_b / len(watch_rows)
        report.metrics["avg_row_size_bytes"] = round(avg, 1)
        if avg > t.avg_row_size_max_bytes:
            report.operational_warnings.append(
                f"O6: avg row size {avg:.0f} B > threshold {t.avg_row_size_max_bytes} B"
            )

    # ---- O7: ts monotonic --------------------------------------------------
    breaks = 0
    last_ts: Optional[float] = None
    for r in watch_rows:
        cur = r.get("ts")
        if cur is None:
            continue
        if last_ts is not None and cur < last_ts:
            breaks += 1
        last_ts = cur
    if breaks > 0:
        report.operational_warnings.append(
            f"O7: {breaks} timestamp regression(s) in watch_loop rows"
        )
    report.metrics["ts_breaks"] = breaks

    # ---- O8: GPU1 memory growth --------------------------------------------
    if snapshots:
        gpu1 = [s["gpu1_mem_mb"] for s in snapshots if s.get("gpu1_mem_mb") is not None]
        if gpu1:
            growth = max(gpu1) - min(gpu1)
            report.metrics["gpu1_growth_mb"] = growth
            if growth > t.gpu1_growth_max_mb:
                report.operational_warnings.append(
                    f"O8: GPU1 memory grew {growth} MB > threshold {t.gpu1_growth_max_mb}"
                )

    # ---- final verdict ------------------------------------------------------
    if report.critical_violations:
        report.verdict = "FAIL"
    elif report.operational_warnings:
        report.verdict = "DEGRADED"
    else:
        report.verdict = "PASS"

    return report


def _verdict_exit_code(verdict: str) -> int:
    return {"PASS": 0, "FAIL": 1, "DEGRADED": 2}.get(verdict, 1)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-min", type=int, required=True)
    ap.add_argument("--loop-interval", type=float, default=5.0)
    ap.add_argument("--quote-interval", type=float, default=1.0)
    ap.add_argument("--watch-log", required=True)
    ap.add_argument("--quotes-log", required=True)
    ap.add_argument("--trades-log", default=None)
    ap.add_argument("--snapshots", default=None)
    ap.add_argument("--captured-log", default=None,
                    help="optional merged stdout/stderr from the soak jobs")
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--jobs-ok", action="store_true")
    ap.add_argument("--out", required=True,
                    help="output report path (.log); also writes .json sibling")
    args = ap.parse_args(argv)

    captured = ""
    if args.captured_log:
        cp = Path(args.captured_log)
        if cp.exists():
            captured = cp.read_text(encoding="utf-8", errors="replace")

    report = analyze_soak(
        duration_min=args.duration_min,
        loop_interval_sec=args.loop_interval,
        quote_interval_sec=args.quote_interval,
        watch_log=Path(args.watch_log),
        quotes_log=Path(args.quotes_log),
        trades_log=Path(args.trades_log) if args.trades_log else None,
        snapshots_log=Path(args.snapshots) if args.snapshots else None,
        submit=args.submit,
        jobs_ok=args.jobs_ok,
        captured_logs_text=captured,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report.to_text(), encoding="utf-8")
    json_path = out_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(report.to_text())
    return _verdict_exit_code(report.verdict)


if __name__ == "__main__":
    sys.exit(main())
