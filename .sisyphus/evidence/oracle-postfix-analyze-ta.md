# Oracle Post-Fix Review: `analyze_ta.py`

**Reviewer:** Oracle  
**Date:** 2026-06-23  
**Scope:** Verify all 6 fixes from `.sisyphus/plans/fix-analyze-ta.md` were correctly applied.

---

## Bottom Line

**All fixes are correctly applied.** The 4 logic changes (BB/MACD positional access, sort+dedup, atomic write, package init) are implemented exactly per spec, the stale reference check is clean, and all 12 evidence files confirm the results. Runtime verification confirms BB and MACD values are semantically correct.

---

## Fix Verification

### 1. BB Column Order — ✅ Correct

| Expected | Actual (line) | Evidence |
|----------|---------------|----------|
| `bb_upper = bb_df.iloc[:, 2]` | L201 — `bb_df.iloc[:, 2]` | Column order verified: `['BBL_20_2.0_2.0', 'BBM_20_2.0_2.0', 'BBU_20_2.0_2.0', ...]` |
| `bb_mid = bb_df.iloc[:, 1]` | L202 — `bb_df.iloc[:, 1]` | `iloc[:,0]` = BBL (lower), `iloc[:,1]` = BBM (mid), `iloc[:,2]` = BBU (upper) |
| `bb_lower = bb_df.iloc[:, 0]` | L203 — `bb_df.iloc[:, 0]` | ✅ per `t2-column-order.txt` |

**Runtime** (`t2-bb-macd-columns-valid.txt`): `bb_upper ok: True`, `bb_mid ok: True`, `bb_lower ok: True`  
**Oracle runtime**: Upper ≥ Mid ≥ Lower across all 81 valid rows — ✅

### 2. MACD Column Order — ✅ Correct

| Expected | Actual (line) | Evidence |
|----------|---------------|----------|
| `macd = macd_df.iloc[:, 0]` | L191 — `macd_df.iloc[:, 0]` | Column order verified: `['MACD_12_26_9', 'MACDh_12_26_9', 'MACDs_12_26_9']` |
| `macd_hist = macd_df.iloc[:, 1]` | L192 — `macd_df.iloc[:, 1]` | `iloc[:,0]` = MACD line, `iloc[:,1]` = MACDh, `iloc[:,2]` = MACDs |
| `macd_signal = macd_df.iloc[:, 2]` | L193 — `macd_df.iloc[:, 2]` | ✅ per `t2-column-order.txt` |

**Runtime** (`t2-bb-macd-columns-valid.txt`): `macd ok: True`, `macd_hist ok: True`, `macd_signal ok: True`  
**Oracle runtime**: `macd_hist == macd - macd_signal` across all 67 valid rows (tolerance 1e-10) — ✅

### 3. Sort + Dedup — ✅ Correct

| Requirement | Status | Location |
|-------------|--------|----------|
| `sort_values("timestamp")` | ✅ Present | L80 |
| `drop_duplicates(subset=["timestamp"], keep="last")` | ✅ Present | L81 |
| `reset_index(drop=True)` | ✅ Present | L82 |
| `parse_dates=["timestamp"]` | ✅ Still present | L79 |

**Evidence** (`t3-sorted-deduped.txt`): `monotonic: True`, `dups: 0`, `rows: 1000`

### 4. Atomic Write — ✅ Correct

| Requirement | Status | Location |
|-------------|--------|----------|
| `tempfile.mkstemp()` used | ✅ | L533 |
| `shutil.move()` for atomic rename | ✅ | L564 |
| Error cleanup: close fd | ✅ | L567–569 |
| Error cleanup: unlink temp | ✅ | L571–573 |
| `import shutil` | ✅ | L44 |
| `import tempfile` | ✅ | L46 |
| `import os` | ✅ | L43 |

**Evidence** (`t4-concurrent-write.txt`): Both concurrent instances completed, CSV has 1000 rows, sorted, no dups — **PASS**  
**Evidence** (`t4-no-orphan-tmp.txt`): 0 orphan `.tmp` files — ✅

### 5. Package Structure — ✅ Correct

| Requirement | Status | Location |
|-------------|--------|----------|
| `trade_scripts/__init__.py` exists | ✅ | Empty file (1 byte) |
| Package importable | ✅ | `t1-initpy-import.txt`: `package_ok` |
| `from trade_scripts.analyze_ta import load_ta_latest` | ✅ | Oracle verified: `import OK` |

### 6. No Stale References — ✅ Correct

| Check | Result |
|-------|--------|
| `grep -r "trade-scripts" *.py` | No matches |
| `grep -r "trade-scripts" *.toml` | No matches |
| `grep -r "trade-scripts" *.yml/*.yaml/*.json/*.cfg/*.ini` | No matches |

The only remaining `trade-scripts` references are in `.sisyphus/plans/fix-analyze-ta.md` (the plan describing the old name) and `.sisyphus/evidence/oracle-review-analyze-ta.md` (the historical review) — both explicitly excluded per plan. ✅

### 7. All Evidence Files — ✅ Complete

12 task-specific evidence files exist with meaningful results:

| File | Status | Content |
|------|--------|---------|
| `t1-initpy-exists.txt` | ✅ | File exists (1 byte) |
| `t1-initpy-import.txt` | ✅ | `package_ok` |
| `t2-column-order.txt` | ✅ | BB/MACD column order confirmed |
| `t2-runs-without-keyerror.txt` | ✅ | No KeyError, output file created |
| `t2-bb-macd-columns-valid.txt` | ✅ | All 6 columns have non-NaN values |
| `t3-sorted-deduped.txt` | ✅ | monotonic=True, dups=0 |
| `t4-atomic-write-basic.txt` | ✅ | File created successfully |
| `t4-csv-readable.txt` | ✅ | 1000 rows, 23 columns |
| `t4-no-orphan-tmp.txt` | ✅ | 0 orphan tmp files |
| `t4-concurrent-write.txt` | ✅ | PASS: 1000 rows, sorted, no dups |
| `t5-no-stale-refs.txt` | ✅ | No stale refs outside plan/review |
| `t6-integration-test.txt` | ✅ | ALL CHECKS PASSED: True |

Plus 4 final verification files (F1–F4) also exist and contain meaningful results.

---

## Runtime Verification

### BB Value Verification

Computed BB(20, 2σ) on 100-row synthetic series with `ta.bbands()`:
- Column order: `['BBL_20_2.0_2.0', 'BBM_20_2.0_2.0', 'BBU_20_2.0_2.0', 'BBB_20_2.0_2.0', 'BBP_20_2.0_2.0']`
- `iloc[:,2]` mean = 49996.21 (upper) > `iloc[:,1]` mean = 49992.96 (mid) > `iloc[:,0]` mean = 49989.70 (lower)
- `Upper >= Mid` across all 81 valid rows: **True**
- `Mid >= Lower` across all 81 valid rows: **True**
- `iloc[:,2] > iloc[:,0]` always: **True**

### MACD Value Verification

Computed MACD(12/26/9) on same series with `ta.macd()`:
- Column order: `['MACD_12_26_9', 'MACDh_12_26_9', 'MACDs_12_26_9']`
- `iloc[:,0]` = MACD line, `iloc[:,1]` = MACDh (histogram), `iloc[:,2]` = MACDs (signal)
- `macd_hist == macd - macd_signal` across all 67 valid rows (1e-10 tolerance): **True**
- Sample: MACD=-0.331417, Hist=-0.160355, Signal=-0.171062 → MACD - Signal = -0.160355 ✅

**Verdict:** The `iloc` indices are correctly mapped to their named columns.

---

## Issues Found

**None.** All fixes are correctly applied, all evidence files are present and meaningful, and the runtime verification confirms correct behavior.

### Minor Observations (not issues)

1. The `load_ta_series()` function doesn't sort/dedup the enriched CSV (it reads `_ta.csv` which is already sorted at write time) — this is correct per plan ("sort is deterministic, same input → same output").

---

## Verdict

### ✅ **PASS** — all fixes correct

| Fix | Result |
|-----|--------|
| BB positional column access | ✅ Correct (L199–203) |
| MACD positional column access | ✅ Correct (L189–193) |
| Sort + dedup in `load_csv()` | ✅ Correct (L77–83) |
| Atomic write in `save_enriched_csv()` | ✅ Correct (L510–574) |
| `trade_scripts/__init__.py` | ✅ Exists and importable |
| No stale `trade-scripts` references | ✅ Clean |
| All 12 evidence files | ✅ Present and meaningful |
| Runtime value verification | ✅ BB/MACD values semantically correct |
