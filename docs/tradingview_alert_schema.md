# TradingView Alert JSON Schema

This is the wire format that the `POST /webhook/ai-swing` endpoint
expects. Send it from TradingView's alert message body as JSON.

## Top-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `secret` | string | YES | Must match the `AI_SWING_WEBHOOK_SECRET` env var. Compared with `hmac.compare_digest`. Always redacted in audit logs. |
| `source` | string | no | Default `"tradingview"`. Free-text source tag. |
| `strategy` | string | no | Default `"twtx_fibo_v4"`. The Pine script name + version. |
| `symbol` | string | YES | Symbol used by the strategy (e.g. `"TXFR1"`, `"MTXR1"`). |
| `timeframe` | string | no | Pine's `timeframe.period`. e.g. `"5"`, `"15"`. |
| `bar_time` | string | no | Pine's `time` (UTC ISO 8601 if you format it). |
| `open` `high` `low` `close` | number | YES | Current bar OHLC. |
| `prev_ohlc` | object | YES | Prev bar OHLC. See below. |
| `fibo` | object | YES | Fibo computation. See below. |
| `rsi` | object | YES | RSI snapshot. See below. |
| `pivot` | object | YES | Last pivot levels. See below. |
| `signal` | object | YES | Boolean signals from the strategy. See below. |

## `prev_ohlc`

```json
{
  "open":  23000.0,
  "high":  23080.0,
  "low":   22970.0,
  "close": 23055.0
}
```

All four numeric. Used by the strategy's `base = (Prev O+H+L+C)/4`.

## `fibo`

```json
{
  "base":            23026.25,
  "range":            110.0,
  "nearest_ratio":     0.382,
  "nearest_price":   23068.27,
  "touch_support":     true,
  "touch_resistance": false,
  "zone":            "support"
}
```

- `base` = `(prev_ohlc.open + prev_ohlc.high + prev_ohlc.low + prev_ohlc.close) / 4`
- `range` = SetA range or SetB range, whichever your Pine script uses
- `nearest_ratio` = the Fibo ratio nearest to `close` (e.g. `0.382`, `0.5`, `0.618`, `+2.618`, `-2.618`)
- `nearest_price` = the price at that ratio
- `touch_support` = true when `ratio <= 0.382`
- `touch_resistance` = true when `ratio >= 0.618`
- `zone` = one of `"support" | "resistance" | "neutral" | "extreme_above" | "extreme_below"`

## `rsi`

```json
{
  "value":    28.5,
  "ma":       42.1,
  "state":    "oversold",
  "bull_div": true,
  "bear_div": false
}
```

- `state` = one of `"overbought" | "extreme_overbought" | "neutral" | "oversold" | "extreme_oversold"`
- `bull_div` / `bear_div` = your Pine's RSI divergence flags

## `pivot`

```json
{
  "last_high": 23110.0,
  "last_low":  22960.0
}
```

The last confirmed pivot high / pivot low (e.g. `ta.pivothigh` /
`ta.pivotlow` outputs once they confirm).

## `signal`

```json
{
  "buy":         false,
  "sell":        false,
  "combo_buy":   true,
  "combo_sell":  false,
  "div_buy":     false,
  "div_sell":    false,
  "touch_fibo":  true
}
```

- `buy` / `sell` = base buy / sell from the strategy
- `combo_buy` / `combo_sell` = combination of buy/sell + Fibo touch + RSI extreme (the canonical Pine alert)
- `div_buy` / `div_sell` = divergence-only signal
- `touch_fibo` = either `touch_support` or `touch_resistance` is true

## Full example

```json
{
  "secret": "<configured secret>",
  "source": "tradingview",
  "strategy": "twtx_fibo_v4",
  "symbol": "TMFR1",
  "timeframe": "5",
  "bar_time": "2026-05-20T14:55:00Z",
  "open":  23015.0,
  "high":  23022.0,
  "low":   22995.0,
  "close": 23018.0,
  "prev_ohlc": {
    "open": 23000.0, "high": 23080.0, "low": 22970.0, "close": 23055.0
  },
  "fibo": {
    "base": 23026.25,
    "range": 110.0,
    "nearest_ratio": 0.382,
    "nearest_price": 23068.27,
    "touch_support": true,
    "touch_resistance": false,
    "zone": "support"
  },
  "rsi": {
    "value": 28.5,
    "ma": 42.1,
    "state": "oversold",
    "bull_div": true,
    "bear_div": false
  },
  "pivot": {
    "last_high": 23110.0,
    "last_low":  22960.0
  },
  "signal": {
    "buy": false, "sell": false,
    "combo_buy": true, "combo_sell": false,
    "div_buy": true, "div_sell": false,
    "touch_fibo": true
  }
}
```

## Audit

Every inbound alert writes one redacted row to
`logs/ai_swing_webhook.jsonl`. The original `secret` value is
replaced with `"***"` before write. The `payload` field carries the
rest of the body so the operator can grep an alert by `bar_time` or
`symbol`.
