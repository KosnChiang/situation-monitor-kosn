# Live-capture Calibration Runbook

> Mock-only operator procedures for deriving a real
> `config/capture.yaml` calibration block from a live TradingView
> screenshot. Last updated: post-soak + gpu_profile. Pinned to
> commits 9a475c0 / 639c728 / 2e43db4 / c205bd4 / 7cfc06d / a51ad72 /
> 38a280b.

This runbook is what bridges the Phase 5.C / 5.D calibration code
path (already shipped) with reality. The wizard
`scripts/calibrate_chart_dry_run.ps1` never modifies
`config/capture.yaml`; the operator's final step is a manual paste.

---

## 1. 何時需要做 calibration

Run this procedure whenever ANY of the following changes:

* TradingView chart vertical zoom (mouse wheel / `+ -` keys)
* TradingView window resize (any change in pixel height)
* The symbol or its price range (e.g. switch XAUUSD ↔ TXFI)
* `logs/capture_test.png` was re-captured AND the chart geometry
  visibly differs from the previous capture

`config/capture.yaml` ships with the calibration block commented
out → fallback `pixel_y = last` (raw) → watch loop will always emit
`signal=FLAT` against real quotes. Filling the block correctly is
the one manual step needed to wire real prices into the pipeline.

---

## 2. 抓 capture（前置）

```powershell
cd C:\Trading\ai_fibo_vision_trader
$env:LIVE_TRADING='false'; $env:EXECUTION_MODE='mock'; $env:BROKER_MODE='mock'
$env:CUDA_VISIBLE_DEVICES='1'
.\.venv\Scripts\python.exe -m tools.capture_test
```

Expected output:

```
OK: captured monitor=2 region=(None,None,NonexNone) shape=(2160, 3840, 3) -> logs\capture_test.png
CUDA_VISIBLE_DEVICES=1
```

If TradingView is not maximised on monitor 2, fix that first (the
shape should match your TradingView window). See
`docs/phase5e_watch_loop_runbook.md` for the capture setup.

---

## 3. 讀 2 個 reference 價格

Open `logs/capture_test.png` in any image viewer that shows mouse
pixel coordinates. Free options that work on Windows 11:

* **Paint** (`mspaint logs\capture_test.png`) — coordinates in the
  bottom-left status bar.
* **IrfanView** — coordinates in title bar; precise zoom.
* **GIMP** — coordinates in bottom-left.
* **Greenshot** — for ad-hoc measurement.

Procedure:

1. In TradingView's **right-side price ladder**, pick **two distinct
   integer price labels** that span most of the chart vertically.
   Good candidates: round-number labels like `$2400`, `$2200`, or a
   horizontal-ray line you drew yourself.
2. Move the mouse to each label's **horizontal centre line** and
   note the `y` pixel coordinate from the viewer's status bar.
3. Record on paper:
   * `pixel_y_high`: row near the top of the chart (smaller number)
   * `price_high`: the label at that row (numerically larger price)
   * `pixel_y_low`: row near the bottom of the chart (larger number)
   * `price_low`: the label at that row (numerically smaller price)

**Sanity check** (the wizard re-checks; do this on paper first):
* `pixel_y_high < pixel_y_low` (screen y grows downward)
* `price_high > price_low` (TradingView top = higher price)

---

## 4. Validate + visualise via wizard

Non-interactive (recommended once you trust your 4 numbers):

```powershell
.\scripts\calibrate_chart_dry_run.ps1 `
    -PixelYHigh 200 -PriceHigh 2400 `
    -PixelYLow 1900 -PriceLow 2200
```

Interactive (loops on bad numbers; useful first time):

```powershell
.\scripts\calibrate_chart_dry_run.ps1 -Interactive
```

The wizard:

1. Refuses if `$env:LIVE_TRADING / EXECUTION_MODE / BROKER_MODE` are
   hot, or if any broker credential env is set.
2. Calls `tools.calibrate_chart` with the 4 numbers (this is the
   Phase-5.A validation tool; it has not been modified).
3. Writes the visualisation overlay to
   `logs/calibration_debug.png` (gitignored as `logs/*.png`).
4. **Stages** the proposed YAML block to
   `logs/calibration_proposal.log` (gitignored as `logs/*.log`).
5. Shows a before/after diff for the `config/capture.yaml`
   calibration region (READ-ONLY; wizard never writes the config).
6. Opens the overlay PNG in the default viewer (pass `-NoOpenViewer`
   to skip).

Expected output ends with the "next steps" block listing the manual
paste + validation commands.

---

## 5. 視覺驗證 (Layer 1 of 3)

Open `logs/calibration_debug.png`. You should see:

* **Cyan crosshairs** at the two reference points you picked (with
  their price labels).
* **Green tick ladder** drawn at every `--tick` price interval
  (default $10).

The green ladder MUST overlap TradingView's own price grid lines
within ±2 px. If misaligned:

* Misaligned **uniformly** by some amount → your 4 numbers were off
  by a constant (e.g. you measured the label TEXT row not the line
  row); re-pick and re-run.
* Misaligned by an **increasing** amount toward one end → axis is
  log-scale, not linear; switch TradingView to linear scale (Y-axis
  context menu → `Linear`) and re-capture.
* `--tick` lines absent → check that the wizard exit code was 0 and
  that `logs/calibration_debug.png` was actually re-created (file
  timestamp newer than your run).

---

## 6. 結構驗證 (Layer 2 of 3)

Once the overlay aligns, manually paste the staged YAML into
`config/capture.yaml`:

1. `code config\capture.yaml` (or any editor).
2. Find the existing `# calibration:` commented block (around lines
   54-60 in the shipped config).
3. **Replace the entire commented block** with the YAML from
   `logs/calibration_proposal.log` (the lines between the
   `BEGIN PROPOSED YAML` / `END PROPOSED YAML` markers).
4. **Remove any leading `#` prefixes** on the active lines —
   `calibration:`, the `reference_*` keys, `pixel_y`, `price` must
   all be uncommented.
5. Save.

Then validate the parser accepts it:

```powershell
.\.venv\Scripts\python.exe -c "from vision.chart_calibration import ChartCalibration; print(ChartCalibration.from_yaml('config/capture.yaml'))"
```

Expected output: a single line like
`ChartCalibration(pixel_y_high=200, price_high=2400.0, pixel_y_low=1900, price_low=2200.0)`.

If you instead see a `CalibrationError` traceback:
* `pixel_y values must be int` → you saved a decimal (e.g. `200.5`);
  round to the nearest integer pixel.
* `pixel_y_high (...) must be < pixel_y_low (...)` → axis inverted;
  swap reference_high / reference_low.
* `price_high (...) must be > price_low (...)` → same fix.

---

## 7. 端對端驗證 (Layer 3 of 3)

Run one iteration of the watch loop AGAINST your real
`config/capture.yaml` (not the demo fixture) and inspect what
calibration produces. This is a dry-run; no MockExecutor calls.

```powershell
$env:LIVE_TRADING='false'; $env:EXECUTION_MODE='mock'; $env:BROKER_MODE='mock'
$env:CUDA_VISIBLE_DEVICES='1'

# 1. Synthesise one quote at a price you can EYE-CHECK against the chart.
#    Pick a price that TradingView shows as roughly mid-chart.
$check_price = 2300.0
$quote = @{ symbol = 'XAUUSD'; bid = $check_price - 0.25; ask = $check_price + 0.25
            last = $check_price; mid = $check_price; ts = 1.0
            timestamp = '2026-05-18T00:00:00+00:00'; source = 'mock' } | ConvertTo-Json -Compress
Set-Content -Path logs\quotes_calibration_check.jsonl -Value $quote -Encoding utf8

# 2. Run one loop iteration.
.\.venv\Scripts\python.exe -m tools.watch_fibo_loop `
    --offline-image logs\capture_test.png `
    --config        config\capture.yaml `
    --quotes-file   logs\quotes_calibration_check.jsonl `
    --watch-log     logs\watch_loop_calibration_check.jsonl `
    --max-iterations 1 --interval 0
```

Read the resulting row:

```powershell
$row = Get-Content logs\watch_loop_calibration_check.jsonl | Select-Object -Last 1 | ConvertFrom-Json
"calibration.pixel_y = $($row.calibration.pixel_y)"
"filter.top_y       = $($row.filter.top_y)"
"signal.side        = $($row.signal.side)"
```

Interpretation:

* `calibration.status == 'ok'` AND `calibration.pixel_y` close to the
  row of the price you picked on the chart → calibration good.
* `signal.side == 'LONG'` → the price coincidentally landed on a
  filtered Fibo line. Bonus: end-to-end smoke passed.
* `signal.side == 'FLAT'` → price is just far from the Fibo line.
  This is NOT a calibration failure; it's the expected behaviour
  when there is no nearby Fibo line.
* `calibration.status == 'invalid'` → § 6 fix.

---

## 8. Operator Checklist（每次抓新 capture 後跑）

- [ ] `cd C:\Trading\ai_fibo_vision_trader`
- [ ] `git status` — branch + tree clean
- [ ] `.\scripts\test.ps1` — full suite green
- [ ] `$env:LIVE_TRADING, $env:EXECUTION_MODE, $env:BROKER_MODE` — `false / mock / mock` or empty
- [ ] `Get-ChildItem env: | Where-Object Name -match 'SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO'` — no output
- [ ] TradingView maximised on monitor 2; chart visible
- [ ] `python -m tools.capture_test` — `OK ... shape=(...)` matches your TradingView window
- [ ] Open `logs/capture_test.png`; record 4 numbers (§ 3)
- [ ] `.\scripts\calibrate_chart_dry_run.ps1 -PixelYHigh ... -PriceHigh ... -PixelYLow ... -PriceLow ...` — exit 0
- [ ] § 5 visual: green tick ladder overlaps TradingView grid within ±2 px
- [ ] § 6 manual paste into `config/capture.yaml`; `ChartCalibration.from_yaml` returns object (not exception)
- [ ] § 7 end-to-end: `calibration.status='ok'`
- [ ] `git diff config/capture.yaml` — confirm ONLY the calibration block changed; no surprise edits
- [ ] `.\scripts\test.ps1` — full suite still green
- [ ] `.\scripts\watch_loop_smoke.ps1` — still PASS (smoke uses the demo fixture, not your edit; this confirms you didn't break anything)
- [ ] Delete staging artefacts (optional):
      `Remove-Item logs\calibration_proposal.log, logs\calibration_debug.png, logs\quotes_calibration_check.jsonl, logs\watch_loop_calibration_check.jsonl -EA SilentlyContinue`

---

## 9. 故障排除 + Rollback

| Symptom | Likely cause | Fix |
|---|---|---|
| `Refusing to run: LIVE_TRADING=true` | Shell envelope is hot | Open fresh PowerShell |
| `tools.calibrate_chart` exits 2 with `axis_inverted` | swap `_high` / `_low` | wizard's local check catches this before python runs |
| Wizard prints empty proposal | `tools.calibrate_chart` stdout did not contain `calibration:` | re-run with smaller `--tick`; check python stderr |
| Tick ladder uniformly offset | reference point pixel_y measured to label TEXT not LINE | re-pick (zoom in; pixel-perfect mouse) |
| Tick ladder progressively offset | TradingView is on log scale | switch to linear scale (right-click Y axis → Linear) and re-capture |
| `ChartCalibration.from_yaml` raises `CalibrationError` | YAML has decimals in `pixel_y` or axis-inverted | § 6 fix list |
| `watch_loop` always FLAT after calibration | Quote price is just far from any Fibo line | NOT a calibration bug; verify with § 7 mid-chart quote |
| `git diff config/capture.yaml` shows changes outside the calibration block | Editor added trailing whitespace / changed line endings | `git checkout -- config/capture.yaml`; redo edit; configure editor to preserve LF if the file is LF |
| Need to roll back: revert `config/capture.yaml` | | `git checkout -- config/capture.yaml` (fallback to commented block) |

**Rollback to commented-out calibration**: `git checkout -- config/capture.yaml`. The watch loop will revert to `calibration.status="not_set"` → fallback raw → `signal=FLAT`. No code change required.

---

## 10. 參考連結

* `vision/chart_calibration.py` — the 2-point linear transform (Phase 5.A)
* `tools/calibrate_chart.py` — the validator + visualiser (Phase 5.A)
* `tests/test_chart_calibration.py` — unit tests for the transform
* `tests/test_mock_fibo_signal_calibration.py` — end-to-end via signal generator
* `tests/test_phase5c_latest_quote_pipeline.py` — subprocess tests through the latest_quote path
* `docs/phase5e_watch_loop_runbook.md` — what the operator does after calibration is in place
* `docs/phase5_soak_runbook.md` — long-run verification
