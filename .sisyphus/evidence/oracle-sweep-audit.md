# Oracle Sweep Rate Audit — `sweep_rate` Metric Bug

**Auditor**: Oracle (strategic technical advisor)
**Subject**: `sweep_rate` metric in `BacktestHarness.compute_metrics()`
**Date**: 2026-06-23

---

## Bottom Line

**The 100% sweep rate is a metric bug, not a genuine property of the data.** The denominator `total_liquidity_zones` counts all detected zones correctly, but the numerator `swept_count` uses `LiqSwept.notna()` which treats the sentinel value `0.0` (unswept) the same as a valid sweep index. Since `Liquidity` and `LiqSwept` are always written together in the same code branch, `swept_count` always equals `total_liquidity_zones`, producing a tautological 100%.

Actual sweep rates across the crypto datasets are **90–95%**, with 14–24 unswept zones per asset that remain at the end of the dataset.

---

## Trace Findings

### Computation Path

**`smc.liquidity()` (lines 728–853 of `smartmoneyconcepts/smc.py`):**

1. Arrays `liquidity`, `liquidity_level`, `liquidity_end`, `liquidity_swept` are initialized with `np.full(n, np.nan)` — all NaN.
2. For each swing high/low index `i`, a forward scan checks if a later candle reaches the sweep target:
   - **Bullish**: `ohlc_high[i+1:] >= range_high` → `swept = i + 1 + argmax(cond)`, or `swept = 0` if not found.
   - **Bearish**: `ohlc_low[i+1:] <= range_low` → `swept = i + 1 + argmax(cond)`, or `swept = 0` if not found.
3. Nearby swing points within `range_percent` are grouped. If `len(group_levels) > 1`, the zone is recorded.
4. **When a zone is recorded, ALL FOUR output arrays are written on the same code branch** (lines 804–807 or 842–845):
   ```python
   liquidity[i] = 1               # or -1
   liquidity_level[i] = avg_level
   liquidity_end[i] = group_end
   liquidity_swept[i] = swept     # swept is either int index OR 0
   ```
5. **Crucially**: `swept = 0` is the sentinel for "not swept" (no future candle reached the level), but `0` is a valid non-NaN float, so `liquidity_swept[i]` is written as `0.0` — NOT NaN.

**`compute_metrics()` (lines 592–610 of `backtest.py`):**

```python
liq = report["Liquidity"]
total_liq = int(liq.notna().sum())                         # denominator

liq_swept = report["LiqSwept"]
swept_count = int((liq.notna() & liq_swept.notna()).sum())  # numerator
sweep_rate = round(swept_count / total_liq, 4) if total_liq > 0 else 0.0
```

### Numerator and Denominator

| Variable | Expression | What it counts | Value |
|----------|-----------|----------------|-------|
| `total_liquidity_zones` (denom) | `liq.notna().sum()` | Rows where zone was detected (`len(group_levels) > 1`) | 313 (BTC) |
| `swept_count` (numerator) | `(liq.notna() & liq_swept.notna()).sum()` | Rows where both are non-NaN — which is **always the same set** | 313 (BTC) |
| **Result** | `swept_count / total_liq` | **1.0 (100%) always** | **BUG** |

**The `LiqSwept.notna()` filter is a no-op.** It never excludes anything because `LiqSwept` is written as `0.0` (not NaN) for unswept zones. The real sweep status signal is `LiqSwept > 0`.

---

## Empirical Evidence

Analysis of all four per-candle reports confirms unswept zones exist:

| Dataset | Rows | Total Zones | Swept (>0) | Unswept (==0) | Reported Rate | Actual Rate |
|---------|------|-------------|-------------|---------------|---------------|-------------|
| BTCUSDT | 19,376 | 313 | 289 | 24 | 100% | **92.33%** |
| SOLUSDT | 12,852 | 215 | 195 | 20 | 100% | **90.70%** |
| ADAUSDT | 17,925 | 248 | 227 | 21 | 100% | **91.53%** |
| BNBUSDT | 18,891 | 255 | 241 | 14 | 100% | **94.51%** |

The unswept zones are genuine — they include both early-dataset zones (e.g., BTC zone at index 11 with `LiqLevel=3869.17`, where the minimum future low of 2817.0 never reached the bearish target of 2635.34) and late-dataset zones (e.g., BTC zone at index 19180 where only 196 remaining candles exist for the sweep to occur).

### Verification: No Liq/LiqSwept Column Discrepancy

```
Zones with Liq non-NaN but LiqSwept NaN: 0
Zones with Liq NaN but LiqSwept non-NaN: 0
```

The two columns are perfectly correlated — always written together, never one without the other. This is why `swept_count` always equals `total_liq`.

---

## Verdict

### Which Interpretation Applies?

**Neither A nor B as stated.** The actual behavior is:

- **Interpretation A** ("Eventually swept") is what the metric *thinks* it's measuring, but the metric is broken.
- **Interpretation B** ("Active unswept zones") is what the data actually contains — zones that exist at the end of the dataset without being swept.
- The `Swept` column correctly distinguishes swept (`>0`) from unswept (`0`) zones using the sentinel value `0`. **The column semantics are correct.** The bug is only in the metric computation that fails to read this signal.

### Q1: What is `total_liquidity_zones`?

**Count of rows where `Liquidity` is non-NaN.** This equals the number of detected liquidity zones (where `len(group_levels) > 1` in `smc.liquidity()`). This count is correct.

### Q2: What is `sweep_rate`?

**`sum(Liq non-NaN AND LiqSwept non-NaN) / sum(Liq non-NaN)`** — which is always `total_liq / total_liq = 1.0`. This is a **tautology**, not an actual sweep rate. It should be `sum(LiqSwept > 0) / sum(Liq non-NaN)`.

### Q3: Can a zone have NO swept value?

**Yes**, but `LiqSwept` stores `0.0` (not NaN) for unswept zones. This is the correct sentinel design — the forward scan in `smc.liquidity()` assigns `swept = 0` when no future candle reaches the target level. The metric's use of `.notna()` fails to distinguish this sentinel from a genuine sweep index.

### Q4: Is there an off-by-one that guarantees every zone gets swept?

**No. There is no off-by-one or reverse-temporal ordering.** The algorithm is a straightforward forward scan. Zones near the end simply run out of data before a sweep can occur. The 14–24 unswept zones per dataset prove this empirically.

---

## Fix

### Required Code Change

In `compute_metrics()` in `backtest.py`, change lines 607–610:

**Current (buggy):**
```python
# Sweep rate: fraction of liquidity zones that got swept
liq_swept = report["LiqSwept"]
swept_count = int((liq.notna() & liq_swept.notna()).sum())
sweep_rate = round(swept_count / total_liq, 4) if total_liq > 0 else 0.0
```

**Fixed:**
```python
# Sweep rate: fraction of liquidity zones that got swept
liq_swept = report["LiqSwept"]
swept_count = int((liq.notna() & (liq_swept > 0)).sum())
unswept_count = total_liq - swept_count
sweep_rate = round(swept_count / total_liq, 4) if total_liq > 0 else 0.0
```

### Additional Changes to Consider

The metrics dict should also export `unswept_zones` for diagnostics:

```python
# In the return dict, add:
"unswept_zones": unswept_count,
```

This enables detecting when datasets are too short relative to the sweep window — a potential quality flag.

### Why NOT exclude final N bars?

The sentinel `swept=0` already correctly captures "not swept within available data." Excluding the final N bars from the denominator would hide end-of-dataset effects that might be valuable to know about. The `unswept_zones` count is more transparent: you can see exactly how many zones weren't swept, and investigate whether that's a data-length issue or a genuine market signal.

### Effort Estimate

**Quick (<1h)** — single-character change from `.notna()` to `> 0` in the `swept_count` line, plus one new `unswept_zones` variable and dict entry. No test file changes needed (existing tests will now see non-100% rates). Run the cross-market eval to regenerate golden files.

---

## Action Plan

1. **Fix the numerator** in `compute_metrics()`: change `liq_swept.notna()` to `(liq_swept > 0)` — this correctly distinguishes swept zones (valid index ≥ 1) from unswept zones (sentinel 0).
2. **Add `unswept_zones` metric** to the return dict for transparency.
3. **Re-run cross-market validation** — sweep rates will drop from 100% to 90–95%, and the crypto-eval report's liquidity section (§4) will need updating to reflect actual rates.
4. **Update `.sisyphus/evidence/crypto-eval.txt`** — replace the "✅ No anomalies" line for sweep rate with the correct values and a note that unswept zones are expected at dataset end.

---

## Escalation Triggers

| Condition | Would Justify |
|-----------|---------------|
| Sweep rate drops below 50% for a dataset with >10k rows | Indicates the `range_percent` parameter is too tight for that asset's volatility — may need adaptive pip_range |
| `unswept_zones / total_liq` ratio correlates strongly with dataset length | Suggests the datasets are systematically too short — consider requiring minimum data length relative to expected zone width |
| A liquidity zone with index far from end-of-data has `LiqSwept=0` | Less common but can happen (e.g., BTC zone at index 11). Verify the pip_range calculation is appropriate for the asset's volatility range |

---

## Appendix: Full Data Trace for BTC Zone at Index 11

- **Type**: Bearish liquidity (`Liquidity = -1`)
- **Level**: 3869.17
- **Sweep target**: `low_level - pip_range = 3869.17 - 1233.83 = 2635.34`
- **pip_range**: `(max_high - min_low) * 0.01 = (126199.63 - 2817.0) * 0.01 = 1233.83`
- **Minimum low in remaining 19,364 candles**: 2817.0
- **Result**: Low never reached 2635.34 → `swept = 0` → correctly unswept
- **Metric reported**: swept ✅ (false positive — counted because `0.0` is not NaN)
