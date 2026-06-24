# Test Plan: `analyze_ta.py` — Comprehensive Test Suite

## TL;DR
> **Quick Summary**: Build a complete pytest suite for `trade_scripts/analyze_ta.py` — 19 testable components across pure functions, I/O integration, and system-level CLI — with 35-row synthetic data fixtures, BB/MACD invariant checks, and explicit capture of the latent `rsi_label(NaN) → "overbought"` bug.
> **Deliverables**: `pyproject.toml` pytest config, `tests/conftest.py` fixtures, `tests/test_analyze_ta.py` (∼60 tests), fix for the NaN bug in `rsi_label`.
> **Estimated Effort**: Medium (1–2 days)
> **Parallel Execution**: YES — 5 waves + final verification
> **Critical Path**: T1 (config) → T2 (fixtures) → [T3,T4,T5] (unit tests) → [T6,T7] (integration) → T8 (system) → T9 (fix)

---

## Context

### Original Request
Generate a test plan for `trade_scripts/analyze_ta.py` based on the Oracle scoping report (`.sisyphus/evidence/oracle-test-plan-scope.md`), covering pytest infrastructure, synthetic data fixtures, unit tests for 10 pure functions, integration tests for I/O functions, system tests, and the latent NaN bug.

### Interview Summary
The Oracle report identified **19 testable components** across 4 tiers (unit, integration, system, CLI) with **∼60 tests** needed. Key findings:

1. **Minimum 35 rows** of synthetic OHLCV data are required for full MACD signal line warmup (26 + 9 periods).
2. **Constant price series** (`close = 50000`) gives deterministic indicator values: BB_mid=50000, BB_width=0, MACD=0, RSI=50.
3. **BB column order** was fixed (verified by post-fix review): `bb_upper=iloc[:,2]`, `bb_mid=iloc[:,1]`, `bb_lower=iloc[:,0]`.
4. **MACD column order** was fixed: `macd=iloc[:,0]`, `macd_hist=iloc[:,1]`, `macd_signal=iloc[:,2]`.
5. **`rsi_label(float("nan"))`** returns `"overbought"` because `nan < 30` is `False` in Python. This is a **latent bug**.
6. **`mfi_signal`** IS NaN-safe (uses `math.isnan`), but `rsi_label` is not.
7. **`macd_signal_label`** — complex branching logic with 9 edge cases, flagged as "most bug-prone" by Oracle.
8. **No pytest config exists** in `pyproject.toml`. Need to add `[tool.pytest.ini_options]`. (pytest is already declared in `[dependency-groups.dev]`.)
9. **No `conftest.py`** exists yet in `tests/`.
10. **Existing test pattern** uses `sys.path.insert(0, ...)` + plain `assert` + `pytest` for discovery.

### Metis Review

**Questions that should have been asked:**
1. Should `macd_signal_label` (the most P0-complex function with 9 edge cases) be included in the test scope? The user did not list it explicitly, but it is P0 and "most bug-prone" per Oracle. **Decision**: Include it — it is critical path code called by `print_timeframe_block` and used downstream.
2. Should `print_timeframe_block` be tested via `capsys` snapshot? **Decision**: Yes — snapshot testing catches formatting regressions.
3. Should pytest-cov be added for coverage? **Decision**: Defer — the priority is correctness coverage, not metric targets. Can be added later.
4. How should imports work? `sys.path.insert(0, ...)` is the existing pattern. **Decision**: Keep the pattern for consistency, do NOT switch to editable installs.

**Guardrails:**
- Do NOT modify `analyze_ta.py` except for T9 (the NaN fix).
- Do NOT write tests that depend on real exchange data or external APIs.
- Do NOT create files outside `tmp_path` fixtures or the evidence directory.
- Do NOT add CI configuration (GitHub Actions, etc.) — out of scope.
- Do NOT split the test file into multiple files — one `test_analyze_ta.py` per the Oracle recommendation.

**Assumptions requiring validation:**
1. `uv run pytest tests/test_analyze_ta.py` is the intended execution command.
2. `pytest.mark.parametrize` is acceptable for table-driven test cases.
3. Unused imports in test files are acceptable if they document available fixtures.
4. The `tmp_path` built-in fixture is sufficient for I/O tests.

**Scope creep areas to lock down:**
1. Property-based testing (Hypothesis) — explicitly excluded.
2. Pre-computed indicator snapshot CSV for `compute_indicators` — Oracle suggested it as optional "later" item. Exclude from initial plan.
3. Cross-timeframe integration tests — excluded. Each timeframe is tested independently.
4. Coverage thresholds — note target but do not enforce in CI (no CI scope).

**Missing acceptance criteria:**
1. All tests must pass with `uv run pytest tests/test_analyze_ta.py -v`.
2. The NaN bug fix must change behavior — `rsi_label(float("nan"))` must return a non-"overbought" string.
3. Evidence files must be created for all QA scenarios in `.sisyphus/evidence/`.

---

## Work Objectives

### Core Objective
Build a comprehensive, deterministic, and maintainable test suite for `analyze_ta.py` that achieves ≥90% line coverage on P0 functions and catches regressions in indicator computation, derived signal labeling, I/O correctness, and the latent NaN bug.

### Concrete Deliverables
1. **`pyproject.toml`** — updated with `[tool.pytest.ini_options]` (pytest is already declared in `[dependency-groups.dev]`)
2. **`tests/conftest.py`** — 4 synthetic OHLCV fixtures (constant, linear, step, sinusoidal) + temp_dir fixture
3. **`tests/test_analyze_ta.py`** — ∼60 tests organized by component class
4. **Bug fix** — `rsi_label()` NaN guard added (in `analyze_ta.py`)
5. **Evidence directory** — `.sisyphus/evidence/t{N}-{name}.txt` for every QA scenario

### Definition of Done
- [ ] `uv run pytest tests/test_analyze_ta.py -v` passes with 0 failures
- [ ] `rsi_label(float("nan"))` returns `"insufficient data"` (or similar) — not `"overbought"`
- [ ] BB ordering invariant: `bb_upper >= bb_mid >= bb_lower` for all valid rows
- [ ] MACD identity invariant: `macd_hist == macd - macd_signal` for all valid rows
- [ ] All 23 output columns present in `compute_indicators` output
- [ ] All QA scenarios produce evidence files in `.sisyphus/evidence/`
- [ ] No orphan temp files left by `save_enriched_csv` tests
- [ ] No modifications to `analyze_ta.py` except the `rsi_label` NaN fix

### Must Have
- All Oracle-identified test cases for P0 components
- BB/MACD invariant checks (column ordering)
- NaN edge case coverage for all label functions
- Agent-executable QA scenarios per task
- Constant-price known-value assertions

### Must NOT Have (Guardrails)
- ❌ No CI/CD configuration (no `.github/workflows/`, no `.gitlab-ci.yml`)
- ❌ No Hypothesis or property-based testing
- ❌ No real exchange data fetches
- ❌ No pre-computed snapshot CSV files in `tests/test_data/`
- ❌ No modification of `analyze_ta.py` beyond T9
- ❌ No splitting tests across multiple files
- ❌ No Docker or containerized test setup

---

## Verification Strategy

### Test Decision
**TDD for bug fix** (T9): Write the failing NaN test first (RED), then fix `rsi_label` (GREEN), then refactor if needed.
**Tests-after for everything else**: Write all tests against the existing code.

### QA Policy
All verification is agent-executed. Every task must produce evidence files at `.sisyphus/evidence/t{N}-{task-name}.txt`. Evidence files contain the raw output of the verification commands — do NOT summarize or truncate them.

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Foundation)
  T1: pytest config ───────────────────┐
  T2: conftest.py fixtures ────────────┤  (independent)
                                        │
Wave 2 (Unit Tests — Pure Functions)   │
  T3: normalize, last_valid, ema, ─────┤
      macd_signal_label                │  (parallel with T4, T5)
  T4: rsi, mfi, obv_signal, bb_label ─┤
  T5: _macd_cross, _obv_slope, ───────┤
      _price_vs_bb                     │
                                        │
Wave 3 (Integration — I/O Functions)   │
  T6: load_csv, load_ta_latest, ───────┤  (depends on T2 fixtures)
      load_ta_series                    │
  T7: compute_indicators, ─────────────┤  (depends on T2 fixtures)
      save_enriched_csv                │
                                        │
Wave 4 (System Tests)                  │
  T8: analyze_timeframe, print_block, ──┤  (depends on T6, T7)
      parse_args/main                   │
                                        │
Wave 5 (Bug Fix)                       │
  T9: Fix rsi_label NaN bug ───────────┤  (depends on T4 NaN test)
                                        │
Final Verification (blocking)          │
  F1–F4: Compliance, Quality, ─────────┘  (depends on all T tasks)
          Suite Run, Scope
```

### Dependency Matrix

| Task | Depends On | Blocks | Parallel With |
|------|-----------|--------|---------------|
| T1 | — | T2 | — |
| T2 | T1 | T3,T4,T5,T6,T7,T8 | — |
| T3 | T2 | T8 | T4,T5 |
| T4 | T2 | T8,T9 | T3,T5 |
| T5 | T2 | T8 | T3,T4 |
| T6 | T2 | T8 | T7 |
| T7 | T2 | T8 | T6 |
| T8 | T3,T4,T5,T6,T7 | F1-F4 | — |
| T9 | T4 | — | — |
| F1-F4 | T1-T9 | — | — |

### Agent Dispatch Summary

| Task | Agent Profile | Skills Needed |
|------|--------------|---------------|
| T1 | python-config | pyproject.toml, uv |
| T2 | python-test-infra | pytest fixtures, pandas, numpy |
| T3 | python-unit-tester | parametrize, pandas, pytest |
| T4 | python-unit-tester | parametrize, edge cases, NaN handling |
| T5 | python-unit-tester | parametrize, numpy, pandas Series |
| T6 | python-integration | tempfile, CSV, pandas I/O |
| T7 | python-integration | pandas_ta, indicators, CSV |
| T8 | python-system | capsys, argparse, CLI |
| T9 | python-bugfix | NaN handling, math module |
| F1 | oracle | Compliance audit |
| F2 | unspecified-high | Code quality review |
| F3 | unspecified-high | Full suite execution |
| F4 | deep | Scope fidelity check |

---

## TODOs

### Wave 1 — Foundation

---

#### T1. Configure pytest in `pyproject.toml`

**What to do**:
1. Add a `[tool.pytest.ini_options]` section to `pyproject.toml` with:
   - `testpaths = ["tests"]`
   - `python_files = ["test_*.py"]`
   - `python_classes = ["Test*"]`
   - `python_functions = ["test_*"]`
   - `filterwarnings = ["ignore::DeprecationWarning"]`
   - `addopts = "-v --tb=short"`
   (pytest is already declared in `[dependency-groups.dev]` — no need to add to `[project.dependencies]`.)

**Must NOT do**:
- Do NOT add `[tool.coverage]` sections — coverage is out of scope.
- Do NOT modify any other section of `pyproject.toml`.
- Do NOT use `--no-header` or other pytest options that suppress output needed for evidence.

**References**:
- Existing `pyproject.toml` lines 1-14
- Oracle: "No pytest configuration exists yet in `pyproject.toml`"

**Acceptance Criteria**:
- `uv run pytest --version` prints a version string
- `uv run pytest tests/ --collect-only` discovers zero tests (no test file exists yet — passes with "no tests collected") or only existing tests
- `uv run pytest --help` shows the ini_options took effect

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest --version 2>&1` | Output contains `pytest 8` or `pytest 9` | `.sisyphus/evidence/t1-pytest-version.txt` |
| 2 | Bash | `uv run pytest tests/ --collect-only -q 2>&1` | Returns exit code 0 (may say "no tests collected") | `.sisyphus/evidence/t1-collect-only.txt` |
| 3 | Bash | `uv run python -c "import pytest; print(pytest.__version__)" 2>&1` | Prints version without ImportError | `.sisyphus/evidence/t1-importable.txt` |

---

#### T2. Create `tests/conftest.py` with All Synthetic Data Fixtures

**What to do**:
Create `tests/conftest.py` with the following fixtures:

1. **`synthetic_ohlcv(n, base, step)`** — factory function (not a fixture) that generates deterministic OHLCV data:
   - `n` rows (default 35), `base` price (default 50000.0), `step` (default 10.0)
   - Columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`
   - Uses `pd.date_range("2024-01-01", periods=n, freq="1h")` for timestamps
   - `close = base + np.arange(n) * step` (linear uptrend)
   - `open = np.roll(close, 1)` with `open[0] = close[0]` (no gap)
   - `high = np.maximum(open, close) * 1.002`
   - `low = np.minimum(open, close) * 0.998`
   - `volume = np.full(n, 100.0)`

2. **`constant_ohlcv`** fixture — wraps `synthetic_ohlcv()` with `step=0.0` → all closes = 50000.0

3. **`step_ohlcv`** fixture — 35 rows, half at 50000.0, half at 50100.0 (sharp jump)

4. **`sinusoidal_ohlcv`** fixture — 35 rows, `close = 50000 + 500 * sin(t)` over `[0, 2π]`

5. **`tmp_csv_dir`** fixture — `tmp_path` factory that creates a `data/` subdirectory and returns the `Path`

6. Add `sys.path.insert(0, ...)` helper to make `trade_scripts` importable (consistent with existing test patterns)

**Must NOT do**:
- Do NOT import `pytest` unnecessarily (it's automatically available in conftest.py)
- Do NOT use `yield_fixture` — use the simpler `@pytest.fixture` pattern
- Do NOT write any test functions in conftest.py

**References**:
- Oracle: "Synthetic Data Fixture Design" section (rows 47-163)
- Existing `tests/test_structure_engine.py` line 13 for `sys.path` pattern
- `analyze_ta.py` constants: `TOP_DOWN_TIMEFRAMES`, `TF_LABELS`

**Acceptance Criteria**:
- `synthetic_ohlcv(35)` returns a DataFrame with shape `(35, 6)` and correct column names
- `synthetic_ohlcv(35)` has `close` starting at 50000.0 and incrementing by 10.0
- `constant_ohlcv` fixture has all `close == 50000.0`
- All fixtures are deterministic (same output on every call)
- `tmp_csv_dir.readable()` resolves and is writable

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | Write a one-shot script that imports fixtures, creates 35-row df, prints shape and first/last close | `(35, 6)`, close[0]=50000, close[34]=50340 | `.sisyphus/evidence/t2-fixture-shape.txt` |
| 2 | Bash | Write a one-shot script that creates constant_ohlcv and checks all close == 50000.0 | all close values are 50000.0 | `.sisyphus/evidence/t2-constant-price.txt` |
| 3 | Bash | Write a one-shot script that creates step_ohlcv and checks first half close=50000, second half close=50100 | split confirmed | `.sisyphus/evidence/t2-step-function.txt` |
| 4 | Bash | Write a one-shot script that verifies all 4 fixtures are deterministic (call twice, assert identical) | All four assert passes | `.sisyphus/evidence/t2-deterministic.txt` |

---

### Wave 2 — Unit Tests (Pure Functions)

---

#### T3. Unit Tests: `normalize_symbol`, `last_valid`, `ema_signal`, `macd_signal_label`

**What to do**:
Add to `tests/test_analyze_ta.py`:

1. **`class TestNormalizeSymbol`** — 4 parametrized cases:
   - `("BTC/USDT", "BTCUSDT")`
   - `("BTCUSDT", "BTCUSDT")`
   - `("", "")`
   - `("BTC/USDT/ABC", "BTCUSDTABC")`

2. **`class TestLastValid`** — 6 parametrized cases using `pd.Series`:
   - All valid: `[1.0, 2.0, 3.0]` → `3.0`
   - Trailing NaN: `[1.0, 2.0, nan]` → `2.0`
   - All NaN: `[nan, nan]` → `nan`
   - Single element: `[42.0]` → `42.0`
   - Single NaN: `[nan]` → `nan`
   - Leading NaN: `[nan, 5.0]` → `5.0`
   - Use `math.isnan()` for NaN assertions

3. **`class TestEmaSignal`** — 5 parametrized cases:
   - `(110, 100)` → contains `"above"` and `"+10.0%"`
   - `(90, 100)` → contains `"below"` and `"-10.0%"`
   - `(100.05, 100)` → contains `"at"`
   - `(100, 100)` → contains `"at"` and `"+0.0%"`
   - `(1, 0)` → no crash (division by zero edge case)

4. **`class TestMacdSignalLabel`** — 10 parametrized cases (P0 — most bug-prone):
   - Bullish crossover: macd prev=-1 curr=1, signal=0, hist prev=-1 curr=1 → `"bullish crossover —"`
   - Bearish crossover: macd prev=1 curr=-1, signal=0, hist prev=1 curr=-1 → `"bearish crossover —"`
   - Bullish widening: macd curr=2 > sig=1, hist +1 (abs > prev +0.5) → `"bullish (widening) —"`
   - Bullish converging: macd curr=2 > sig=1, hist +0.5 (abs < prev +1) → `"bullish (converging) —"`
   - Bearish widening: macd curr=0.5 < sig=1, hist -0.8 (abs > prev -0.3) → `"bearish (widening) —"`
   - Bearish converging: macd curr=0.5 < sig=1, hist -0.3 (abs < prev -0.8) → `"bearish (converging) —"`
   - Insufficient data (0 or 1 valid row) → `"insufficient data"`
   - All NaN → `"insufficient data"`
   - Zero hist: macd curr=2 > sig=1, hist=0 → `"bullish (converging) —"` (abs(0) < abs(0.5))
   - Sparse valid indices: macd=[1,NaN,NaN,NaN,NaN,2], signal=[0,NaN,NaN,NaN,NaN,0], hist=[0.5,NaN,NaN,NaN,NaN,1] → uses indices 0 and 5 as prev/curr, returns `"bullish (widening) —"` (verifies non-adjacent valid rows don't crash and don't incorrectly return "insufficient data")
   - Each case passes 2+ row `pd.Series` for each parameter

**Must NOT do**:
- Do NOT test `macd_signal_label` with scalar inputs — it expects Series
- Do NOT use `pytest.approx()` where exact equality works
- Do NOT assert on exact string formatting for `ema_signal` (floating point precision varies)

**References**:
- `analyze_ta.py` lines 63-65 (`normalize_symbol`)
- `analyze_ta.py` lines 68-71 (`last_valid`)
- `analyze_ta.py` lines 322-329 (`ema_signal`)
- `analyze_ta.py` lines 332-363 (`macd_signal_label`)
- Oracle: Edge cases sections 3.1, 3.2, 3.6, 3.7

**Acceptance Criteria**:
- All 4 test classes pass with `uv run pytest tests/test_analyze_ta.py::TestNormalizeSymbol tests/test_analyze_ta.py::TestLastValid tests/test_analyze_ta.py::TestEmaSignal tests/test_analyze_ta.py::TestMacdSignalLabel -v`

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestNormalizeSymbol or TestLastValid or TestEmaSignal or TestMacdSignalLabel" -v 2>&1` | All tests PASS, 0 failures | `.sisyphus/evidence/t3-unit-tests.txt` |

---

#### T4. Unit Tests: `rsi_label`, `mfi_signal`, `obv_signal`, `bb_label`

**What to do**:
Add to `tests/test_analyze_ta.py`:

1. **`class TestRsiLabel`** — 11 parametrized cases:
   - `29.9` → contains `"oversold"`
   - `30.0` → contains `"bearish"`
   - `39.9` → contains `"bearish"`
   - `40.0` → contains `"neutral-bearish"`
   - `49.9` → contains `"neutral-bearish"`
   - `50.0` → contains `"neutral-bullish"`
   - `59.9` → contains `"neutral-bullish"`
   - `60.0` → contains `"bullish"`
   - `69.9` → contains `"bullish"`
   - `70.0` → contains `"overbought"`
   - **`float("nan")`** → should NOT contain `"overbought"` (this will FAIL — marks the latent bug)

2. **`class TestMfiSignal`** — 11 parametrized cases:
   - `19.9` → contains `"oversold"`
   - `20.0` → contains `"bearish"`
   - `39.9` → contains `"bearish"`
   - `40.0` → contains `"neutral-bearish"`
   - `49.9` → contains `"neutral-bearish"`
   - `50.0` → contains `"neutral-bullish"`
   - `59.9` → contains `"neutral-bullish"`
   - `60.0` → contains `"bullish"`
   - `79.9` → contains `"bullish"`
   - `80.0` → contains `"overbought"`
   - `float("nan")` → contains `"insufficient data"`

3. **`class TestObvSignal`** — 7 parametrized cases. Each case provides a DataFrame with a single `obv` column:
   - Rising: `[100, 110, 120, 130, 140]` → `"confirming uptrend"`
   - Falling: `[140, 130, 120, 110, 100]` → `"confirming downtrend"`
   - Neutral: `[100, 101, 102, 103, 104]` → `"neutral / choppy"`
   - Boundary rising: `[100, 100, 100, 105, 105]` (exactly 5.0% change) → `"confirming uptrend"`
   - Boundary falling: obv change exactly -5.0% → `"confirming downtrend"`
   - Insufficient data: 4 non-NaN rows → `"insufficient data"`
   - Exactly at threshold (4.999%) → `"neutral / choppy"` (below 5.0)

4. **`class TestBbLabel`** — 14 parametrized cases. Each case provides `(close_price, upper, mid, lower, upper_arr, lower_arr)`:
   - Near upper: close=99, upper=100, lower=0, pct_b=0.99 → `"near upper band"`
   - Near lower: close=1, upper=100, lower=0, pct_b=0.01 → `"near lower band"`
   - Mid-range: close=50, upper=100, lower=0, pct_b=0.5 → `"mid-range"`
   - Upper half: close=80, upper=100, lower=0, pct_b=0.8 → `"upper half"`
   - Lower half: close=20, upper=100, lower=0, pct_b=0.2 → `"lower half"`
   - Band width zero: upper=100, lower=100 → `"band width zero"`
   - Squeeze: upper_arr with 11 values, last width avg=10, current=8 (0.8x < 0.9) → contains `"squeezing"`
   - Expansion: upper_arr with 11 values, last width avg=10, current=12 (1.2x > 1.1) → contains `"expanding"`
   - No squeeze: upper_arr with 11 values, current width within 0.9-1.1x → no "squeezing" or "expanding"
   - Insufficient squeeze data: upper_arr with < 11 values → no expansion suffix
   - Boundary pct_b=0.95 → `"near upper band"`
   - Boundary pct_b=0.05 → `"near lower band"`
   - Boundary pct_b=0.4 → `"lower half"` (outside 0.4-0.6)
   - Boundary pct_b=0.6 → `"upper half"` (outside 0.4-0.6)

**Must NOT do**:
- Do NOT test `rsi_label(NaN)` with `pytest.raises` — it does not raise, it silently returns wrong value
- Do NOT use `==` for string comparison on `ema_signal`/`rsi_label`/`mfi_signal` — use `in` or `startswith`
- Do NOT create real CSV files for `obv_signal` — construct DataFrames in-memory

**References**:
- `analyze_ta.py` lines 366-379 (`rsi_label`)
- `analyze_ta.py` lines 382-397 (`mfi_signal`)
- `analyze_ta.py` lines 400-417 (`obv_signal`)
- `analyze_ta.py` lines 420-458 (`bb_label`)
- Oracle: Edge cases sections 3.8, 3.9, 3.10, 3.11
- Oracle: "rsi_label does NOT handle NaN — latent bug" (line 272)

**Acceptance Criteria**:
- All tests pass EXCEPT the `rsi_label(NaN)` test (expected to fail — marks the bug)
- `uv run pytest tests/test_analyze_ta.py -k "TestRsiLabel or TestMfiSignal or TestObvSignal or TestBbLabel" -v` reports at least 1 FAILURE (the NaN test)

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestRsiLabel or TestMfiSignal or TestObvSignal or TestBbLabel" -v 2>&1` | At least 1 FAIL (NaN test), rest PASS | `.sisyphus/evidence/t4-unit-tests.txt` |
| 2 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestRsiLabel and test_rsi_nan" -v 2>&1` | FAILED — confirms NaN bug exists | `.sisyphus/evidence/t4-nan-bug-confirmed.txt` |

---

#### T5. Unit Tests: `_macd_cross`, `_obv_slope`, `_price_vs_bb`

**What to do**:
Add to `tests/test_analyze_ta.py`:

1. **`class TestMacdCross`** — 8 parametrized cases. Each provides `(macd_series, signal_series, expected_series)`:
   - Bullish: MACD=[-1,-0.5,0.5,1], Signal=[0,0,0,0] → ["none","none","bullish_cross","none"]
   - Bearish: MACD=[1,0.5,-0.5,-1], Signal=[0,0,0,0] → ["none","none","bearish_cross","none"]
   - No cross: MACD=[1,2,3], Signal=[0,0,0] → ["none","none","none"]
   - NaN middle: MACD=[1,NaN,3], Signal=[0,0,0] → NaN row skipped, result at index 2 "none"
   - All NaN → All "none"
   - Single row → All "none"
   - Cross at bar 1: MACD=[-1,1], Signal=[0,0] → ["none","bullish_cross"]
   - Equal touching: MACD=[0,0], Signal=[0,0] → ["none","none"] (no cross)

2. **`class TestObvSlope`** — 9 parametrized cases. Each provides `(obv_series, expected_last_value)`:
   - Rising: `[0,1,2,3,4]` → last value `1.0`
   - Falling: `[4,3,2,1,0]` → last value `-1.0`
   - Flat: `[10,10,10,10,10]` → last value `0.0`
   - Low magnitude: `[100,100,100,100,100.5]` → last value `0.0` (ratio < 0.01)
   - All zero: `[0,0,0,0,0]` → NaN (mean=0)
   - NaN in window: `[1,NaN,3,4,5]` → NaN at index 4, all earlier NaN
   - Insufficient: 4 rows → all NaN
   - Large values: `[1e6,2e6,3e6,4e6,5e6]` → last value `1.0`
   - Ratio at threshold: values such that ratio == 0.01 → `0.0` (not > 0.01)

3. **`class TestPriceVsBb`** — 7 parametrized cases. Each provides `(close, upper, lower, expected_series)`:
   - Above upper: close=110, upper=100, lower=80 → ["above_upper"]
   - Below lower: close=70, upper=100, lower=80 → ["below_lower"]
   - Inside: close=90, upper=100, lower=80 → ["inside"]
   - Exactly on upper: close=100, upper=100, lower=80 → ["inside"]
   - Exactly on lower: close=80, upper=100, lower=80 → ["inside"]
   - Close NaN: close=NaN, upper=100, lower=80 → ["none"]
   - All NaN: all NaN → ["none"]

**Must NOT do**:
- Do NOT test these functions via the public API (`compute_indicators`) — test them directly
- Do NOT assume the output Series has the same index as input — use `.tolist()` for comparison
- Do NOT use `pd.testing.assert_series_equal` for string Series — check `.tolist()` instead

**References**:
- `analyze_ta.py` lines 236-254 (`_macd_cross`)
- `analyze_ta.py` lines 257-295 (`_obv_slope`)
- `analyze_ta.py` lines 298-316 (`_price_vs_bb`)
- Oracle: Edge cases sections 3.3, 3.4, 3.5

**Acceptance Criteria**:
- All test classes pass with `uv run pytest tests/test_analyze_ta.py -k "TestMacdCross or TestObvSlope or TestPriceVsBb" -v`

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestMacdCross or TestObvSlope or TestPriceVsBb" -v 2>&1` | All PASS, 0 failures | `.sisyphus/evidence/t5-unit-tests.txt` |

---

### Wave 3 — Integration Tests (I/O Functions)

---

#### T6. Integration Tests: `load_csv`, `load_ta_latest`, `load_ta_series`

**What to do**:
Add to `tests/test_analyze_ta.py`:

1. **`class TestLoadCsv`** — uses the `tmp_csv_dir` fixture. Tests:
   - Normal sorted file: write OHLCV CSV, load it, verify shape and sorting
   - Unsorted timestamps: write rows with reversed timestamps, verify they come back sorted ascending
   - Duplicate timestamps: write 2 rows with same timestamp but different values, verify keep="last"
   - Empty file (header only) → empty DataFrame
   - Missing timestamp column → KeyError
   - Extra columns → loads without error, ignores extras
   - Malformed timestamp → pd.read_csv failure

2. **`class TestLoadTaLatest`** — uses the `tmp_csv_dir` fixture. Tests:
   - Normal file: write a proper TA-enriched CSV with all columns, verify dict has all 5 keys
   - Missing file → returns `None`
   - Empty file → returns `None`
   - `mfi14 = NaN` column → returns `None`
   - `obv = NaN` → returns `None`
   - `obv_slope = ""` → returns `0.0`
   - `obv_slope = "none"` → returns `0.0`
   - `obv_slope = 1.0` → returns `1.0`
   - Missing columns `mfi14`/`obv` → returns `None`
   - `close = None` → returns `None` in the dict

3. **`class TestLoadTaSeries`** — uses the `tmp_csv_dir` fixture. Tests:
   - Normal: write 10-row CSV, `tail=3`, verify 3 rows returned
   - `tail > len(df)`: `tail=100` on 10 rows → 10 rows returned
   - Missing file → returns `None`
   - Empty file → returns `None`
   - Timestamp parse: verify returned timestamps are `datetime` dtype
   - Malformed CSV → returns `None`

**Must NOT do**:
- Do NOT write to the real `data/` directory — always use `tmp_csv_dir`
- Do NOT test `load_ta_latest` or `load_ta_series` with real exchange files
- Do NOT assume the enriched CSV has exactly 23 columns in tests — check the expected columns for the specific test case

**References**:
- `analyze_ta.py` lines 77-83 (`load_csv`)
- `analyze_ta.py` lines 89-136 (`load_ta_latest`)
- `analyze_ta.py` lines 139-174 (`load_ta_series`)
- Oracle: Edge cases sections 3.12, 3.13, 3.14

**Acceptance Criteria**:
- All 3 test classes pass with `uv run pytest tests/test_analyze_ta.py -k "TestLoadCsv or TestLoadTaLatest or TestLoadTaSeries" -v`

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestLoadCsv or TestLoadTaLatest or TestLoadTaSeries" -v 2>&1` | All PASS, 0 failures | `.sisyphus/evidence/t6-integration-tests.txt` |

---

#### T7. Integration Tests: `compute_indicators`, `save_enriched_csv`

**What to do**:
Add to `tests/test_analyze_ta.py`:

1. **`class TestComputeIndicators`** — 7 test methods:
   - `test_all_23_columns_present`: Run `compute_indicators` on `synthetic_ohlcv(35)`, verify all 23 expected column names are in `df.columns`
   - `test_insufficient_data_5_rows`: 5-row input → all indicator columns NaN
   - `test_single_row`: 1-row input → no crash, all indicator columns NaN
   - `test_constant_price`: `constant_ohlcv` fixture → BB_mid ≈ 50000, MACD ≈ 0, OBV = 0, BB_width ≈ 0  
     **Note**: RSI-14 may return NaN for constant price (zero stddev) — this is pandas-ta behavior. Verify NaN handling is graceful rather than asserting a specific RSI value.
   - `test_zero_volume`: volume = 0 → MFI = NaN (division by zero)
   - `test_bb_ordering_invariant`: For all rows where BB is valid, `bb_upper >= bb_mid >= bb_lower` (tolerance 1e-10)
   - `test_macd_identity_invariant`: For all rows where MACD is valid, use relative tolerance: `np.isclose(macd_hist, macd - macd_signal, rtol=1e-10, atol=1e-15)`

2. **`class TestSaveEnrichedCsv`** — uses the `tmp_csv_dir` fixture. Tests:
   - `test_normal_write`: `save_enriched_csv` with 35-row df, verify file created, has 23 columns, correct fieldnames in header
   - `test_atomic_write_integrity`: Write CSV, read it back with `load_csv()` (the production code path), verify all rows present and correct — full write→parse round-trip including timestamp parsing and sort+dedup
   - `test_no_orphan_temp_files`: After write, verify no `.tmp` files exist in the output directory
   - `test_nan_handling`: DF with NaN values → CSV has empty strings for NaN cells
   - `test_column_order`: CSV header fieldnames match the exact `fieldnames` list order from the function
   - `test_concurrent_write_safety`: Use `threading.Thread` to write same file from 2 threads, verify the resulting file is a valid CSV parseable by `load_csv()` with all 23 expected columns — validates that atomic writes prevent partial-file reads

**Must NOT do**:
- Do NOT hardcode indicator values for the linear trend fixture — these depend on pandas-ta internals
- Do NOT use `pd.testing.assert_frame_equal` for `compute_indicators` output — only check invariants
- Do NOT run actual multiprocessing for concurrent write test — use `threading` for simplicity

**References**:
- `analyze_ta.py` lines 180-233 (`compute_indicators`)
- `analyze_ta.py` lines 510-574 (`save_enriched_csv`)
- Oracle: Edge cases sections 3.15, 3.16
- Oracle post-fix: BB/MACD column order verified (iloc indices)

**Acceptance Criteria**:
- All test classes pass with `uv run pytest tests/test_analyze_ta.py -k "TestComputeIndicators or TestSaveEnrichedCsv" -v`
- BB ordering invariant holds for all valid rows
- MACD identity invariant holds for all valid rows

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestComputeIndicators or TestSaveEnrichedCsv" -v 2>&1` | All PASS, 0 failures | `.sisyphus/evidence/t7-integration-tests.txt` |
| 2 | Bash | `uv run python -c "from tests.test_analyze_ta import TestComputeIndicators; ..." 2>&1` (one-shot script checking BB ordering + MACD identity explicitly) | BB and MACD invariants hold | `.sisyphus/evidence/t7-bb-macd-invariants.txt` |

---

### Wave 4 — System Tests

---

#### T8. System Tests: `analyze_timeframe`, `print_timeframe_block`, `parse_args`/`main`

**What to do**:
Add to `tests/test_analyze_ta.py`:

1. **`class TestPrintTimeframeBlock`** — uses `capsys` fixture:
   - `test_normal_dataframe`: Build a DataFrame with known indicator values, call `print_timeframe_block`, assert stdout contains expected labels: "EMA21", "MACD", "RSI14", "BB", "MFI14", "OBV", "EBSW", "ATR14", "OBV Slope", "BB Width", "Price vs BB", "EMA21 Slope", "MACD Cross"
   - `test_all_nan_dataframe`: Build DataFrame with all-NaN indicators, call `print_timeframe_block`, assert stdout contains "insufficient data" for relevant indicators

2. **`class TestAnalyzeTimeframe`** — uses `tmp_csv_dir` fixture:
   - `test_normal`: Write a CSV to the tmp dir with OHLCV data, call `analyze_timeframe("BTC/USDT", "1h", tmp_csv_dir)`, assert returns `True`, output CSV exists with all 23 columns
   - `test_missing_input_csv`: Call with non-existent CSV path, assert returns `False`, warning printed to stderr
   - `test_output_csv_has_correct_columns`: Same as normal case, verify enriched CSV has all 23 expected columns

3. **`class TestParseArgs`** — uses `monkeypatch` fixture:
   - `test_default_timeframes`: `sys.argv = ["prog", "BTC/USDT"]` → args.symbol == "BTC/USDT", args.timeframe is None
   - `test_single_timeframe`: `sys.argv = ["prog", "BTC/USDT", "--timeframe", "4h"]` → args.timeframe == "4h"
   - `test_custom_data_dir`: `sys.argv = ["prog", "BTC/USDT", "--data-dir", "/custom/path"]` → args.data_dir == "/custom/path"
   - `test_no_symbol`: `sys.argv = ["prog"]` → `SystemExit` (argparse error)

**Must NOT do**:
- Do NOT call `main()` directly — it calls `sys.exit()` on argparse errors. Test `parse_args()` instead.
- Do NOT test `analyze_timeframe` with the real `data/` directory — always use `tmp_csv_dir`
- Do NOT test `analyze_timeframe` with an actual enriched CSV (it loads raw OHLCV) — create raw OHLCV CSV

**References**:
- `analyze_ta.py` lines 464-507 (`print_timeframe_block`)
- `analyze_ta.py` lines 580-600 (`analyze_timeframe`)
- `analyze_ta.py` lines 606-622 (`parse_args`)
- `analyze_ta.py` lines 625-641 (`main`)
- Oracle: Edge cases sections 3.17, 3.18, 3.19

**Acceptance Criteria**:
- All test classes pass with `uv run pytest tests/test_analyze_ta.py -k "TestPrintTimeframeBlock or TestAnalyzeTimeframe or TestParseArgs" -v`

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestPrintTimeframeBlock or TestAnalyzeTimeframe or TestParseArgs" -v 2>&1` | All PASS, 0 failures | `.sisyphus/evidence/t8-system-tests.txt` |

---

### Wave 5 — Bug Fix

---

#### T9. Fix `rsi_label` NaN Bug

**What to do**:
1. **RED phase** (already done in T4): The test `test_rsi_nan` asserts that `rsi_label(float("nan"))` does NOT return `"overbought"`.
2. **GREEN phase**: Edit `analyze_ta.py` — add NaN guard at the top of `rsi_label()`:
   ```python
   def rsi_label(rsi: float) -> str:
       if math.isnan(rsi):
           return "insufficient data"
       # ... rest of existing logic unchanged
   ```
3. **REFACTOR phase**: Consider extracting the zone constants for RSI and MFI into a shared helper to avoid future inconsistencies. (Optional — do only if clean.)
4. Run the previously-failing NaN test to confirm it now passes.
5. Run the full test suite to confirm no regressions.

**Must NOT do**:
- Do NOT change the behavior for any non-NaN input — zone boundaries must remain identical
- Do NOT add imports that aren't already present (`math` is already imported at line 42)
- Do NOT change `mfi_signal` — it already handles NaN correctly

**References**:
- `analyze_ta.py` lines 366-379 (`rsi_label`)
- `analyze_ta.py` lines 382-397 (`mfi_signal` — reference for correct NaN handling)
- Oracle: "Critical finding: rsi_label does NOT handle NaN" (line 272)

**Acceptance Criteria**:
- `rsi_label(float("nan"))` returns a string containing `"insufficient data"`
- All existing non-NaN RSI labels are unchanged
- Full test suite passes with 0 failures

**QA Scenarios**:

| # | Tool | Steps | Expected Result | Evidence Path |
|---|------|-------|-----------------|---------------|
| 1 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestRsiLabel and test_rsi_nan" -v 2>&1` | PASSED (was FAILED before fix) | `.sisyphus/evidence/t9-nan-fix-confirmed.txt` |
| 2 | Bash | `uv run pytest tests/test_analyze_ta.py -k "TestRsiLabel" -v 2>&1` | All RSI label tests PASS (10 zone tests + NaN test) | `.sisyphus/evidence/t9-rsi-all-pass.txt` |
| 3 | Bash | `uv run pytest tests/ -v 2>&1` | Full suite PASS — 0 failures | `.sisyphus/evidence/t9-full-suite-regression.txt` |
| 4 | Bash | `uv run python -c "from trade_scripts.analyze_ta import rsi_label; print(rsi_label(float('nan')))" 2>&1` | Output contains "insufficient data" (use `in` or `.endswith` — `rsi_label` returns `"nan — insufficient data"` not `== "insufficient data"`) | `.sisyphus/evidence/t9-nan-direct-call.txt` |

---

## Final Verification Wave

All T tasks (T1–T9) must be complete before starting final verification.

---

#### F1. Plan Compliance Audit (oracle)

**What to do**: Verify that every deliverable from this plan exists and is correct.

**Audit checklist**:
- [ ] `pyproject.toml` has `pytest` in `[project.dependencies]`
- [ ] `pyproject.toml` has `[tool.pytest.ini_options]` with testpaths, filterwarnings, addopts
- [ ] `tests/conftest.py` exists with synthetic_ohlcv, constant_ohlcv, step_ohlcv, sinusoidal_ohlcv, tmp_csv_dir
- [ ] `tests/test_analyze_ta.py` exists with all test classes
- [ ] All Oracle-identified edge cases for P0 components are covered
- [ ] `rsi_label(float("nan"))` does NOT return "overbought"
- [ ] Evidence files exist for all QA scenarios (T1–T9)

**Evidence**: `.sisyphus/evidence/f1-compliance-audit.txt`

---

#### F2. Code Quality Review (unspecified-high)

**What to do**: Review the test code for:
- No hardcoded paths
- No test interdependencies (each test is independently runnable)
- No flaky tests (deterministic fixtures)
- Proper use of parametrize (no copy-pasted test functions)
- Clean imports (no unused imports)
- Consistent naming (classes = `Test*`, methods = `test_*`)

**Evidence**: `.sisyphus/evidence/f2-code-quality.txt`

---

#### F3. Full Suite Execution (unspecified-high)

**What to do**: Run the complete test suite and capture output.

```bash
uv run pytest tests/ -v 2>&1
```

**Acceptance**: 0 failures, 0 errors, 0 warnings (excluding known DeprecationWarning filter).

**Evidence**: `.sisyphus/evidence/f3-full-suite.txt`

---

#### F4. Scope Fidelity Check (deep)

**What to do**: Verify no scope creep occurred:
- [ ] No CI/CD files were created
- [ ] No files outside `.sisyphus/evidence/`, `tests/`, or `pyproject.toml` were modified (except `analyze_ta.py` for T9)
- [ ] No test files beyond `test_analyze_ta.py` were created
- [ ] The only change to `analyze_ta.py` is the `rsi_label` NaN guard
- [ ] No real exchange data was used in any test

**Evidence**: `.sisyphus/evidence/f4-scope-fidelity.txt`

---

## Commit Strategy

After final verification passes:

1. **Stage**: `git add pyproject.toml tests/conftest.py tests/test_analyze_ta.py trade_scripts/analyze_ta.py .sisyphus/evidence/`
2. **Commit message**: `test(analyze_ta): comprehensive test suite with NaN bug fix`
3. **Body**:
   ```
   - ~60 tests across 19 components (unit, integration, system)
   - 35-row synthetic OHLCV fixtures for full MACD warmup
   - BB ordering invariant: upper >= mid >= lower
   - MACD identity invariant: hist == macd - signal
   - Fixed latent NaN bug in rsi_label (NaN fell through to "overbought")
   - All indicators verified on constant-price known-value data
   - Agent-executable QA with evidence files
   ```

---

## Success Criteria

- [ ] All tests pass (0 failures)
- [ ] `rsi_label(float("nan"))` returns `"insufficient data"` (was `"overbought"`)
- [ ] BB ordering invariant passes for all valid rows
- [ ] MACD identity invariant passes for all valid rows
- [ ] All 23 output columns present in `compute_indicators`
- [ ] Evidence files exist for all QA scenarios
- [ ] No regressions in existing tests (`test_causality.py`, `test_structure_engine.py`)
- [ ] No orphan temp files from `save_enriched_csv` tests
- [ ] No modifications to `analyze_ta.py` beyond the `rsi_label` NaN fix
