# Pine Alert Patch -- TWTX Fibo + RSI v4

How to emit the JSON expected by `POST /webhook/ai-swing` from a
TradingView Pine v5 script. The full schema is in
`docs/tradingview_alert_schema.md`.

## 1. Compute the components in Pine

Assuming your existing TWTX script already produces:

- `prevOpen prevHigh prevLow prevClose`
- `base = (prevOpen + prevHigh + prevLow + prevClose) / 4`
- `setA_range setB_range` (your two range definitions)
- `nearest_ratio nearest_price` (the Fibo ratio nearest to close)
- `touch_support = ratio <= 0.382`
- `touch_resistance = ratio >= 0.618`
- `zone = "support" | "resistance" | "neutral" | "extreme_above" | "extreme_below"`
- `rsiVal rsiMa`
- `rsiState = "overbought" | "extreme_overbought" | "neutral" | "oversold" | "extreme_oversold"`
- `bullDiv bearDiv`
- `pivot_high pivot_low`
- `sigBuy sigSell sigComboBuy sigComboSell sigDivBuy sigDivSell sigTouchFibo`

## 2. Build the JSON string

Pine has no native JSON builder; concatenate strings carefully and use
`str.tostring(x, "#.##")` for numbers to control precision.

```pine
//@version=5
// ... your existing strategy code above ...

f_b(x) => x ? "true" : "false"

alertJson = '{' +
    '"secret":"' + input.string("CHANGE-ME", "Webhook secret") + '",' +
    '"source":"tradingview",' +
    '"strategy":"twtx_fibo_v4",' +
    '"symbol":"' + syminfo.tickerid + '",' +
    '"timeframe":"' + timeframe.period + '",' +
    '"bar_time":"' + str.tostring(time, "yyyy-MM-dd\'T\'HH:mm:ss\'Z\'") + '",' +
    '"open":' + str.tostring(open, "#.##") + ',' +
    '"high":' + str.tostring(high, "#.##") + ',' +
    '"low":'  + str.tostring(low, "#.##") + ',' +
    '"close":' + str.tostring(close, "#.##") + ',' +
    '"prev_ohlc":{' +
        '"open":'  + str.tostring(prevOpen, "#.##") + ',' +
        '"high":'  + str.tostring(prevHigh, "#.##") + ',' +
        '"low":'   + str.tostring(prevLow,  "#.##") + ',' +
        '"close":' + str.tostring(prevClose,"#.##") +
    '},' +
    '"fibo":{' +
        '"base":'             + str.tostring(base, "#.##") + ',' +
        '"range":'            + str.tostring(setA_range, "#.##") + ',' +
        '"nearest_ratio":'    + str.tostring(nearest_ratio, "#.###") + ',' +
        '"nearest_price":'    + str.tostring(nearest_price, "#.##") + ',' +
        '"touch_support":'    + f_b(touch_support) + ',' +
        '"touch_resistance":' + f_b(touch_resistance) + ',' +
        '"zone":"'            + zone + '"' +
    '},' +
    '"rsi":{' +
        '"value":'    + str.tostring(rsiVal, "#.##") + ',' +
        '"ma":'       + str.tostring(rsiMa,  "#.##") + ',' +
        '"state":"'   + rsiState + '",' +
        '"bull_div":' + f_b(bullDiv) + ',' +
        '"bear_div":' + f_b(bearDiv) +
    '},' +
    '"pivot":{' +
        '"last_high":' + str.tostring(pivot_high, "#.##") + ',' +
        '"last_low":'  + str.tostring(pivot_low,  "#.##") +
    '},' +
    '"signal":{' +
        '"buy":'         + f_b(sigBuy)         + ',' +
        '"sell":'        + f_b(sigSell)        + ',' +
        '"combo_buy":'   + f_b(sigComboBuy)    + ',' +
        '"combo_sell":'  + f_b(sigComboSell)   + ',' +
        '"div_buy":'     + f_b(sigDivBuy)      + ',' +
        '"div_sell":'    + f_b(sigDivSell)     + ',' +
        '"touch_fibo":'  + f_b(sigTouchFibo)   +
    '}' +
'}'
```

## 3. Emit alertcondition + alert

```pine
alertcondition(sigComboBuy or sigComboSell or sigDivBuy or sigDivSell,
               title="TWTX Fibo Combo or Divergence",
               message="will be replaced at alert config time")

// Trigger on any of the canonical signals; operator narrows in the
// TradingView Alert UI as desired.
if (sigComboBuy or sigComboSell or sigDivBuy or sigDivSell)
    alert(alertJson, alert.freq_once_per_bar_close)
```

## 4. Set the alert in TradingView

1. Open the chart with this Pine script attached.
2. Click the bell icon -> Create Alert.
3. Condition: choose the script.
4. Trigger: `alert() function call`.
5. Message: leave the placeholder; the script's `alert(alertJson, ...)`
   overrides it at runtime.
6. Webhook URL: `https://<your-host>/webhook/ai-swing`
7. Save.

## 5. Verify on the server side

Tail `logs/ai_swing_webhook.jsonl`. Every alert should produce one
row with `verdict="processed"` (or `unauthorized` / `disabled` if the
secret is wrong / unset). `logs/chart_context.jsonl` should grow at
the same time, and `logs/ai_swing_decisions.jsonl` should record one
decision per alert (mostly `NO_TRADE` until your rule conditions
trigger).

## 6. Common Pine gotchas

- `time` and `time_close` are Unix milliseconds, not seconds. Format
  with `str.tostring(time, "yyyy-MM-dd'T'HH:mm:ss'Z'")` for ISO.
- `alert.freq_once_per_bar_close` is essential -- without it you'll
  fire on every tick within a bar and DOS the webhook.
- Pine string concatenation is `+`, not `..`.
- Boolean to JSON: `true` / `false` are lowercase. Use the helper
  `f_b(x) => x ? "true" : "false"` -- Pine doesn't auto-lowercase.
- Numeric formatting: control decimals with `"#.##"` / `"#.###"`.
