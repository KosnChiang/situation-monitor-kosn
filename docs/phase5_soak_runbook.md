# Mock Soak Runbook (Phase 5)

> Mock-only operator procedures for running the 30–60 minute soak
> verification on the Phase 5.D watch loop + Phase 5.5 quote feed.
> Last updated: Phase 5.G-soak. Pinned to commits 9a475c0 / 639c728 /
> 2e43db4 / c205bd4 / 7cfc06d.

The soak runs the same Python CLIs the operator uses day-to-day,
isolated to dedicated `*_soak.jsonl` paths so the production logs
(`logs/quotes.jsonl`, `logs/watch_loop.jsonl`, `logs/trades.jsonl`)
are **never** touched.

---

## 1. 一鍵 30-min soak（最常用）

```powershell
cd C:\Trading\ai_fibo_vision_trader
.\scripts\watch_loop_soak.ps1
```

預設行為：

* `-DurationMin 30` — 30 分鐘
* `-LoopInterval 5 s` → 預期 ~360 watch_loop rows
* `-QuoteInterval 1.0 s` → 預期 ~1800 quote rows
* `-BasePrice 2250.0` → 與 `tests/fixtures/capture_calibration_demo.yaml` calibration 對齊（quote → pixel_y=1533，撞到 filtered Fibo 線）
* `-Submit` **NOT** set → 不寫 `trades_soak.jsonl`，純 dry-run
* `-SnapshotIntervalSec 60` → 30 個資源快照

第一次先跑 5-min mini-soak：

```powershell
.\scripts\watch_loop_soak.ps1 -DurationMin 5
```

如果 mini-soak verdict 是 PASS，再去跑 30-min。

---

## 2. soak 寫入哪些檔案（隔離 path）

所有路徑都是 `_soak` 後綴，**production logs 完全不被觸碰**：

| 檔案 | 內容 |
|---|---|
| `logs/quotes_soak.jsonl`        | mock quote feed 的輸出（每 row 一筆 JSON） |
| `logs/watch_loop_soak.jsonl`    | watch loop 的 per-iteration 結構化 row |
| `logs/trades_soak.jsonl`        | 只在 `-Submit` 下產生；`mode=mock` MockExecutor fills；經 `$env:TRADES_LOG` 重導向 |
| `logs/soak_snapshots.jsonl`     | 每 60s 一筆資源 snapshot（process WS, CPU, GPU, jsonl size） |
| `logs/soak_jobs.log`            | quote_feed + watch_loop 兩個 Job 的 stdout/stderr 合併 |
| `logs/soak_report_<ts>.log`     | analyzer 文字報告（人讀） |
| `logs/soak_report_<ts>.json`    | analyzer 結構化報告（machine-readable） |

腳本結尾會印「production logs sanity」區塊列出
`logs/quotes.jsonl` / `logs/watch_loop.jsonl` / `logs/trades.jsonl`
的 size + lastWriteTime — soak 跑前後這些**不應該**改變。

---

## 3. 確認 mock-only（9 層獨立守護）

soak 在 Phase 5.F 的 8 層上再加 1 層 (analyzer C1 audit)：

| Layer | 內容 |
|---|---|
| 1. Soak script refuse-if-hot | 同 5.E/5.F |
| 2. Soak job ScriptBlock 重新 pin envelope | `Start-Job -ScriptBlock { ... $env:LIVE_TRADING='false'; ... }` |
| 3. `$env:TRADES_LOG` 重導向 | 強制 MockExecutor 寫到 `logs/trades_soak.jsonl` 而非 `logs/trades.jsonl` |
| 4. Wrapper layer | Phase 5.E 的 refuse-if-hot 在 watch_loop_run.ps1 / quote_feed_run.ps1（本 soak 直接呼叫 python 因 Start-Job 環境隔離，這層 N/A） |
| 5. Python `_refuse_if_live` | Phase 5.D / 5.5 強制檢查 |
| 6. RiskGate `__init__` | `LiveTradingForbidden`（Phase 1-3） |
| 7. MockExecutor.submit boundary | `LIVE_TRADING / EXECUTION_MODE` 二次檢查 |
| 8. Pre-run wipe targets only `*_soak` files | Phase 5-soak 守住 |
| 9. **analyzer C1 audit** | soak 結束時 analyzer 掃 `trades_soak.jsonl`，任何 `mode != 'mock'` 行 → verdict FAIL + exit 1 + 紅字報告 |

---

## 4. analyzer verdict

`tools.analyze_soak` 退出碼：
* `0` — PASS（所有 critical 和 operational 都過）
* `1` — FAIL（任一 critical violation C1–C6）
* `2` — DEGRADED（critical 全過但 operational warning O1–O8 任一）

### Critical invariants（C1–C6）

| Code | Invariant |
|---|---|
| C1 | 所有 `trades_soak.jsonl` row 必須 `mode == 'mock'` |
| C2 | quote_feed + watch_loop 兩個 Job 都 `State == Completed`（無 Failed / Stopped） |
| C3 | 合併 `soak_jobs.log` 不含 `Traceback / Refusing to / LiveTradingForbidden` |
| C4 | `watch_loop_soak.jsonl` row count 在 `DurationMin*60/LoopInterval ±5%` 範圍內 |
| C5 | `quotes_soak.jsonl` row count 在 `DurationMin*60/QuoteInterval ±5%` 範圍內 |
| C6 | 每 row 都有 12 個必要 top-level keys（schema 對齊 `tests/fixtures/watch_loop_row_schema.json`） |

### Operational warnings（O1–O8）

| Code | Warning | Threshold |
|---|---|---|
| O1 | loop process working-set growth | < 50 MB |
| O2 | feed process working-set growth | < 30 MB |
| O3 | loop CPU average                | < 25% (of one core) |
| O4 | dedupe suppression ratio        | ≥ 50% |
| O5 | LONG signal ratio               | ≥ 80% |
| O6 | avg row size                    | < 1.5 KB |
| O7 | timestamps monotonic            | 100% |
| O8 | GPU 1 memory growth             | < 200 MB |

---

## 5. 看 report

```powershell
# 最新一份 report
Get-Content (Get-ChildItem logs\soak_report_*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
```

範例 PASS 輸出：

```
Phase 5 mock soak verdict
==================================================
  verdict       : PASS
  duration_min  : 30
  submit        : False
  generated_at  : 2026-05-18T...Z

Critical violations: 0
  (none)

Operational warnings: 0
  (none)

Metrics:
  dedupe_fired                   1
  dedupe_ratio                   0.997
  dedupe_suppressed              359
  feed_ws_growth_mb              2
  gpu1_growth_mb                 0
  loop_cpu_avg_pct               4.21
  loop_rows_actual               360
  loop_rows_expected             360.0
  loop_ws_growth_mb              12
  long_ratio                     1.0
  quote_rows_actual              1800
  quote_rows_expected            1800.0
  signal_flat                    0
  signal_long                    360
  trades_count                   0
  ts_breaks                      0
```

### machine-readable

```powershell
$json = Get-Content (Get-ChildItem logs\soak_report_*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName | ConvertFrom-Json
$json.verdict
$json.metrics
```

---

## 6. 變體

```powershell
# 5-min 快驗
.\scripts\watch_loop_soak.ps1 -DurationMin 5

# 60 min（最長建議）
.\scripts\watch_loop_soak.ps1 -DurationMin 60

# Opt-in mock fills（檢查 dedupe + MockExecutor 行為）
.\scripts\watch_loop_soak.ps1 -DurationMin 30 -Submit
#   預期：logs/trades_soak.jsonl 每筆 mode=mock，row 數 ≈ DurationMin*60/DedupeCooldown

# 改 base price、interval、symbol
.\scripts\watch_loop_soak.ps1 -DurationMin 10 -BasePrice 2400 -LoopInterval 3
```

---

## 7. 故障排除

| Symptom | Likely cause | Fix |
|---|---|---|
| `Refusing to run: LIVE_TRADING=true` | Shell envelope is hot | 開新 PowerShell |
| verdict FAIL with C1 violation | trades_soak.jsonl 有 mode != mock | **STOP**；§ 9 emergency rollback；checksum trading core 是否被改 |
| verdict FAIL with C3 violation | 合併 jobs log 有 Traceback | 開 `logs/soak_jobs.log` 找 traceback，看是 watch loop 還是 quote feed crash |
| verdict FAIL with C4/C5 violation | row count 不對 | 看 `logs/soak_jobs.log` 是否一個 job 提早死、`-MaxIterations` 算錯、`-QuoteInterval` 太小 |
| verdict FAIL with C6 violation | watch_loop 寫出非預期 schema row | tools/watch_fibo_loop.py 被改過？對比 git HEAD |
| verdict DEGRADED with O1/O2 | 記憶體可能洩漏 | 看 `logs/soak_snapshots.jsonl` 趨勢；若 linear growth 就是 leak |
| verdict DEGRADED with O4 | dedupe ratio 低 | quote price 是否漂移太大（隨機 walk 跨出 tolerance）？降 quote volatility |
| verdict DEGRADED with O5 | LONG ratio 低（大量 FLAT） | quote price 沒撞到 Fibo line；`-BasePrice` 沒對齊 calibration |
| verdict DEGRADED with O8 | GPU 1 記憶體增長 | offline-image 路徑不該用 CUDA；若有增長表示其他 process 在用 GPU 1（Ollama？）|
| soak 跑完發現 `logs/trades.jsonl` 改變 | $env:TRADES_LOG 沒生效 | **P0 incident**；§ 9；對比 git HEAD 確認 `scripts/watch_loop_soak.ps1` 與 `executor/mock_executor.py` 未被改 |

---

## 8. Operator Checklist（每次跑 soak 前後）

**Soak 前：**

- [ ] `cd C:\Trading\ai_fibo_vision_trader`
- [ ] `git status` — 確認分支 + 沒有未追蹤敏感檔
- [ ] `.\scripts\test.ps1` — 全綠（目前 349+ passing）
- [ ] `$env:LIVE_TRADING, $env:EXECUTION_MODE, $env:BROKER_MODE` — `false / mock / mock` 或空
- [ ] 無 broker credential env 設定（`Get-ChildItem env: | ? Name -match 'SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO'` 無 output）
- [ ] 記錄 production logs 當前 size：`Get-ChildItem logs\quotes.jsonl, logs\watch_loop.jsonl, logs\trades.jsonl -EA SilentlyContinue | Select Name, Length, LastWriteTime`

**Soak 中：**

- [ ] 每 60s 看到 `[soak] snap #N t+Ms loop_pid=... wl_rows=...` 進度
- [ ] 不要打開其他重 GPU 任務（避免 O8 偽陽性）
- [ ] 不要 push、不要切分支

**Soak 後：**

- [ ] 看 `logs/soak_report_<ts>.log` verdict
- [ ] 確認 `logs/quotes.jsonl` / `logs/watch_loop.jsonl` / `logs/trades.jsonl` size 與 lastWriteTime **未變**（與 soak 前比對）
- [ ] PASS 或 DEGRADED：記錄 verdict + metrics 摘要
- [ ] FAIL：§ 9 emergency rollback
- [ ] （可選）`Remove-Item logs\quotes_soak.jsonl, logs\watch_loop_soak.jsonl, logs\trades_soak.jsonl, logs\soak_snapshots.jsonl, logs\soak_jobs.log -EA SilentlyContinue`
- [ ] `git status` — 工作樹乾淨（沒有未預期修改）

---

## 9. Rollback / 緊急停機

verdict FAIL 並且 C1 violation（trades_soak.jsonl 有 mode != mock）：

1. **不要繼續跑 soak / loop / trading 任何東西**。
2. 快照所有 soak artifacts：
   ```powershell
   $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
   New-Item -ItemType Directory "logs\incident_$stamp" | Out-Null
   Copy-Item logs\quotes_soak.jsonl, logs\watch_loop_soak.jsonl, `
             logs\trades_soak.jsonl, logs\soak_snapshots.jsonl, `
             logs\soak_jobs.log, logs\soak_report_*.* `
             "logs\incident_$stamp\"
   ```
3. 快照 envelope：
   ```powershell
   Get-ChildItem env: |
       Where-Object Name -match 'LIVE|EXEC|BROKER|SHIOAJI|IB_|MT5|BINANCE|ALPACA|CTPRO' |
       Out-File "logs\incident_$stamp\envelope.txt"
   ```
4. 對比 trading core 與 HEAD：
   ```powershell
   git diff HEAD -- executor/ risk/ strategy/ tools/mock_fibo_signal.py tools/watch_fibo_loop.py
   ```
5. **不要 push**。問題釐清之前不送任何 commit 到遠端。

---

## 10. 參考連結

* `docs/phase5e_watch_loop_runbook.md` — 前景 watch loop 操作（無 soak）
* `docs/phase5f_scheduler_runbook.md` — Task Scheduler 整合
* `tools/analyze_soak.py` — 分析器原始碼；inline docstring 列出每個 invariant
* `tests/test_analyze_soak.py` — analyzer 單元測試
* `tests/fixtures/soak_watch_loop_clean.jsonl` — 10-row schema example
* `tests/fixtures/watch_loop_row_schema.json` — required top-level keys
