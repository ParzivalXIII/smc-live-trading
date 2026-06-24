#!/usr/bin/env python3
"""
Forensic Analysis: Streaming vs Batch Gap, Open Trade Impact, and New Metrics.

Outputs to .sisyphus/evidence/gap-analysis/ directory.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

import numpy as np
import pandas as pd

# Ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backtest import BacktestConfig, BacktestHarness, replay_phase
from strategies.bos_flip import BOSFlipStrategy

# =============================================================================
# Setup
# =============================================================================

OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
)

PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")

EURUSD_PATH = os.path.join(PROJECT_ROOT, "tests/test_data/EURUSD/EURUSD_15M.csv")
EURUSD_SHORT_PATH = os.path.join(PROJECT_ROOT, "tests/test_data/EURUSD/EURUSD_15M.csv")

DATASETS = [
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_BTCUSDT_4h.csv",
        "name": "BTCUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_SOLUSDT_4h.csv",
        "name": "SOLUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_ADAUSDT_4h.csv",
        "name": "ADAUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/cryptocurrencies/binance_api_BNBUSDT_4h.csv",
        "name": "BNBUSDT-4H",
        "date_column": "time",
        "date_format": None,
    },
    {
        "path": "tests/test_data/EURUSD/EURUSD_15M.csv",
        "name": "EURUSD-15M",
        "date_column": "Date",
        "date_format": "%Y.%m.%d %H:%M:%S",
    },
]

LEVEL_TOLERANCE = 0.001  # 0.1%


# =============================================================================
# Helper: Run harness and extract events
# =============================================================================

def run_eurusd():
    """Run the streaming+batch backtest on EURUSD and return all relevant data."""
    config = BacktestConfig(
        bos_confirmation_window=10,
    )
    strategy = BOSFlipStrategy()
    harness = BacktestHarness(config, strategy_callback=strategy)
    result = harness.run(EURUSD_PATH)

    report = result.report
    structure_engine = result.structure_engine
    trades = result.trades
    equity = result.equity_curve
    metrics = result.metrics

    return report, structure_engine, trades, equity, metrics, result


# =============================================================================
# Item 1: Gap Analysis — Missed vs Matched BOS Comparison
# =============================================================================

def analyze_gap():
    """Compare missed vs matched batch BOS events."""
    print("=" * 70)
    print("ITEM 1: GAP ANALYSIS — Missed vs Matched BOS Comparison")
    print("=" * 70)

    config = BacktestConfig(bos_confirmation_window=10)
    strategy = BOSFlipStrategy()
    harness = BacktestHarness(config, strategy_callback=strategy)
    result = harness.run(EURUSD_PATH)

    report = result.report
    structure_engine = result.structure_engine

    # ---- Collect batch BOS events ----
    batch_bos = []
    for i in range(len(report)):
        bos_val = report.iloc[i]["BOS"]
        if not (np.isnan(bos_val) if isinstance(bos_val, float) else bos_val is None or bos_val == 0):
            bos_dir = 1 if bos_val == 1 else -1
            level = report.iloc[i]["BOSLevel"]
            if not np.isnan(level):
                batch_bos.append({
                    "index": i,
                    "direction": bos_dir,
                    "level": float(level),
                })

    # ---- Collect streaming confirmed BOS ----
    streaming_confirmed = [
        e for e in structure_engine.events
        if e.event_type == "BOS" and e.status == "confirmed"
    ]

    # ---- Match ----
    matched_batch_indices = set()
    matched_streaming_indices = set()

    for bi, batch in enumerate(batch_bos):
        for si, stream in enumerate(streaming_confirmed):
            if si in matched_streaming_indices:
                continue
            if stream.direction != batch["direction"]:
                continue
            if abs(stream.level - batch["level"]) / max(abs(batch["level"]), 1e-10) > LEVEL_TOLERANCE:
                continue
            index_diff = abs(stream.swing_index - batch["index"])
            if index_diff > 5:
                continue
            matched_batch_indices.add(bi)
            matched_streaming_indices.add(si)
            break

    missed_indices = sorted(set(range(len(batch_bos))) - matched_batch_indices)
    matched_indices = sorted(matched_batch_indices)

    print(f"  Total batch BOS: {len(batch_bos)}")
    print(f"  Matched by streaming: {len(matched_indices)}")
    print(f"  Missed by streaming: {len(missed_indices)}")
    print(f"  Match rate: {len(matched_indices)/len(batch_bos)*100:.1f}%")

    # ---- Compute metrics for each event ----
    # For each BOS event (batch), we need to compute:
    #   - direction (bullish/bearish)
    #   - level
    #   - distance_traveled_after_break: max high - level (bullish) or level - min low (bearish)
    #     in the 50 bars after S2+2
    #   - subsequent_return: close[N] - level (bullish) or level - close[N] (bearish)
    #     at N=10, N=20, N=50 bars after

    all_events_data = []

    for bi, batch in enumerate(batch_bos):
        idx = batch["index"]
        direction = batch["direction"]
        level = batch["level"]
        category = "matched" if bi in matched_batch_indices else "missed"

        # The batch BOS is stamped at S1's index.
        # The break scan starts at i+2 (S2+2). 
        # For streaming we need S3 (the 4th swing), but for batch analysis,
        # the break could have happened at any point from S1+2 onwards.
        # We need to find the actual BrokenIndex from the report.
        broken_idx = None
        if not np.isnan(report.iloc[idx]["BrokenIndex"]):
            broken_idx = int(report.iloc[idx]["BrokenIndex"])

        # Distance traveled: look 50 bars after the break (or after S1+2 if no break?)
        start_lookback = idx + 2  # S2+2
        end_lookback = min(start_lookback + 50, len(report) - 1)
        
        if end_lookback <= start_lookback:
            all_events_data.append({
                "batch_index": bi,
                "direction": direction,
                "level": level,
                "category": category,
                "broken_idx": broken_idx,
                "dist_traveled_50": np.nan,
                "ret_10": np.nan,
                "ret_20": np.nan,
                "ret_50": np.nan,
            })
            continue

        highs = report["High"].values[start_lookback:end_lookback + 1]
        lows = report["Low"].values[start_lookback:end_lookback + 1]
        closes = report["Close"].values

        if direction == 1:  # Bullish BOS
            max_high = np.max(highs)
            dist_traveled = max_high - level
            
            # Subsequent returns at N bars
            n_10 = min(start_lookback + 10, len(closes) - 1)
            n_20 = min(start_lookback + 20, len(closes) - 1)
            n_50 = min(start_lookback + 50, len(closes) - 1)
            
            ret_10 = closes[n_10] - level if n_10 > start_lookback else np.nan
            ret_20 = closes[n_20] - level if n_20 > start_lookback else np.nan
            ret_50 = closes[n_50] - level if n_50 > start_lookback else np.nan
        else:  # Bearish BOS
            min_low = np.min(lows)
            dist_traveled = level - min_low
            
            n_10 = min(start_lookback + 10, len(closes) - 1)
            n_20 = min(start_lookback + 20, len(closes) - 1)
            n_50 = min(start_lookback + 50, len(closes) - 1)
            
            ret_10 = level - closes[n_10] if n_10 > start_lookback else np.nan
            ret_20 = level - closes[n_20] if n_20 > start_lookback else np.nan
            ret_50 = level - closes[n_50] if n_50 > start_lookback else np.nan

        all_events_data.append({
            "batch_index": bi,
            "direction": direction,
            "level": level,
            "category": category,
            "broken_idx": broken_idx,
            "dist_traveled_50": dist_traveled,
            "ret_10": ret_10,
            "ret_20": ret_20,
            "ret_50": ret_50,
        })

    df = pd.DataFrame(all_events_data)

    # Save CSV
    csv_path = os.path.join(OUTPUT_DIR, "missed_vs_matched_comparison.csv")
    df.to_csv(csv_path, index=False)
    print(f"\n  Saved: {csv_path}")

    # ---- Statistical summary ----
    missed_df = df[df["category"] == "missed"]
    matched_df = df[df["category"] == "matched"]

    lines = []
    lines.append("=" * 70)
    lines.append("GAP ANALYSIS REPORT")
    lines.append("=" * 70)
    lines.append(f"\nTotal batch BOS events: {len(batch_bos)}")
    lines.append(f"Matched by streaming:  {len(matched_indices)} ({len(matched_indices)/len(batch_bos)*100:.1f}%)")
    lines.append(f"Missed by streaming:   {len(missed_indices)} ({len(missed_indices)/len(batch_bos)*100:.1f}%)")
    lines.append(f"Confirmation window:   10 bars")
    lines.append(f"Level tolerance:       {LEVEL_TOLERANCE} (0.1%)")

    lines.append(f"\n{'─' * 70}")
    lines.append(f"{'Metric':<35} {'Missed (Mean)':<18} {'Matched (Mean)':<18} {'Ratio':<10}")
    lines.append(f"{'─' * 70}")

    metrics_list = ["dist_traveled_50", "ret_10", "ret_20", "ret_50"]
    metric_labels = {
        "dist_traveled_50": "Max break distance (50 bars after S1+2)",
        "ret_10": "Return at 10 bars after S1+2",
        "ret_20": "Return at 20 bars after S1+2",
        "ret_50": "Return at 50 bars after S1+2",
    }

    for m in metrics_list:
        mv = missed_df[m].dropna()
        mav = matched_df[m].dropna()
        if len(mv) > 0 and len(mav) > 0:
            mean_missed = mv.mean()
            mean_matched = mav.mean()
            ratio = mean_missed / mean_matched if mean_matched != 0 else float('inf')
            lines.append(f"{metric_labels[m]:<35} {mean_missed:<18.6f} {mean_matched:<18.6f} {ratio:<10.4f}")
        else:
            lines.append(f"{metric_labels[m]:<35} {'N/A':<18} {'N/A':<18} {'N/A':<10}")

    lines.append(f"\n{'─' * 70}")
    lines.append("Median comparison (robust to outliers)")
    lines.append(f"{'─' * 70}")
    lines.append(f"{'Metric':<35} {'Missed (Med)':<18} {'Matched (Med)':<18}")
    lines.append(f"{'─' * 70}")

    for m in metrics_list:
        mv = missed_df[m].dropna()
        mav = matched_df[m].dropna()
        if len(mv) > 0 and len(mav) > 0:
            med_missed = mv.median()
            med_matched = mav.median()
            lines.append(f"{metric_labels[m]:<35} {med_missed:<18.6f} {med_matched:<18.6f}")
        else:
            lines.append(f"{metric_labels[m]:<35} {'N/A':<18} {'N/A':<18}")

    # ---- Direction distribution ----
    lines.append(f"\n{'─' * 70}")
    lines.append("Direction Distribution")
    lines.append(f"{'─' * 70}")
    for cat in ["missed", "matched"]:
        sub = df[df["category"] == cat]
        bull = len(sub[sub["direction"] == 1])
        bear = len(sub[sub["direction"] == -1])
        lines.append(f"  {cat.capitalize():8s}: Bullish={bull:4d} ({bull/len(sub)*100:5.1f}%), Bearish={bear:4d} ({bear/len(sub)*100:5.1f}%)")

    # ---- Distribution shape info ----
    lines.append(f"\n{'─' * 70}")
    lines.append("Distribution Shapes (std dev, percentiles)")
    lines.append(f"{'─' * 70}")
    for m in metrics_list:
        mv = missed_df[m].dropna()
        mav = matched_df[m].dropna()
        if len(mv) > 0 and len(mav) > 0:
            lines.append(f"\n  {metric_labels[m]}:")
            lines.append(f"    Missed:  mean={mv.mean():.6f}, std={mv.std():.6f}, p25={mv.quantile(0.25):.6f}, p75={mv.quantile(0.75):.6f}, n={len(mv)}")
            lines.append(f"    Matched: mean={mav.mean():.6f}, std={mav.std():.6f}, p25={mav.quantile(0.25):.6f}, p75={mav.quantile(0.75):.6f}, n={len(mav)}")
        else:
            lines.append(f"\n  {metric_labels[m]}: insufficient data")

    # ---- Hypothesis test ----
    lines.append(f"\n{'─' * 70}")
    lines.append("HYPOTHESIS TEST: Is the gap systematically losing high-edge trades?")
    lines.append(f"{'─' * 70}")
    lines.append("""
    H0: Mean subsequent_return(missed) <= Mean subsequent_return(matched)
        → Gap is benign (missed events are not higher-edge than matched)
    H1: Mean subsequent_return(missed) > Mean subsequent_return(matched)
        → Gap is systematically losing high-edge trades
    """)

    for m in ["ret_10", "ret_20", "ret_50"]:
        mv = missed_df[m].dropna()
        mav = matched_df[m].dropna()
        if len(mv) > 0 and len(mav) > 0:
            mean_diff = mv.mean() - mav.mean()
            lines.append(f"  {metric_labels[m]}:")
            lines.append(f"    Mean missed - mean matched = {mean_diff:.6f}")
            if mean_diff > 0:
                lines.append(f"    ▸ MISSED > MATCHED (potential systematic bias)")
            else:
                lines.append(f"    ▸ MISSED <= MATCHED (gap is benign)")
        else:
            lines.append(f"  {metric_labels[m]}: insufficient data")

    # ---- Conclusion ----
    lines.append(f"\n{'─' * 70}")
    lines.append("CONCLUSION")
    lines.append(f"{'─' * 70}")
    
    # Check all three returns
    benign_count = 0
    bias_count = 0
    for m in ["ret_10", "ret_20", "ret_50"]:
        mv = missed_df[m].dropna()
        mav = matched_df[m].dropna()
        if len(mv) > 0 and len(mav) > 0:
            if mv.mean() <= mav.mean():
                benign_count += 1
            else:
                bias_count += 1
    
    if bias_count == 0:
        lines.append("  The timing gap is BENIGN — missed events do not exhibit")
        lines.append("  higher subsequent returns than matched events.")
    elif benign_count >= 2:
        lines.append("  The timing gap is MOSTLY BENIGN — majority of return windows")
        lines.append("  show missed events <= matched events.")
    else:
        lines.append("  The timing gap MAY SHOW SYSTEMATIC BIAS — missed events")
        lines.append("  consistently show higher subsequent returns.")

    report_text = "\n".join(lines)
    report_path = os.path.join(OUTPUT_DIR, "gap-analysis-report.txt")
    with open(report_path, "w") as f:
        f.write(report_text)
    print(f"  Saved: {report_path}")
    print(report_text)

    return df, missed_df, matched_df


# =============================================================================
# Item 2: Open Trade Analysis
# =============================================================================

def analyze_open_trades():
    """Check for open (unclosed) trades at end of each dataset."""
    print("\n" + "=" * 70)
    print("ITEM 2: OPEN TRADE ANALYSIS")
    print("=" * 70)

    results = []

    for ds in DATASETS:
        name = ds["name"]
        path = os.path.join(PROJECT_ROOT, ds["path"])

        if not os.path.exists(path):
            print(f"  {name}: file not found, skipping")
            continue

        config = BacktestConfig(
            date_column=ds["date_column"],
            date_format=ds["date_format"],
        )
        strategy = BOSFlipStrategy()
        harness = BacktestHarness(config, strategy_callback=strategy)
        result = harness.run(path)

        trades_df = result.trades
        metrics = result.metrics
        simulator = None  # we need access to the simulator's position

        # Reconstruct: we need to check the TradeSimulator at end of run
        # Since it's not exposed in BacktestResult, we need to re-analyze the trades
        # 
        # The strategy uses enter_long/enter_short which call _close_existing first.
        # So when a new signal comes in, the old position is closed first.
        # The only way a position stays open at end is if the last signal had no
        # subsequent signal to close it.
        #
        # Let's count closed trades and compute unrealized PnL for any position
        # that would still be open.

        # Actually, let me inspect: the TradeSimulator.close() only closes if position is not None.
        # The last trade might be unclosed. Let's check trades_df for exit_index = None.
        if trades_df is not None and not trades_df.empty:
            # Check if any trade has None for exit_index
            none_exits = trades_df["exit_index"].isna().sum()
            all_exits = len(trades_df)
            print(f"\n  {name}:")
            print(f"    Total trades in log: {all_exits}")
            print(f"    Trades with exit_index=None: {none_exits}")

            # Also check: does to_dataframe() include open positions?
            # No — TradeSimulator.to_dataframe() only uses _closed_trades
            # So open positions are silently dropped!

            # Let's compute unrealized PnL manually by looking at the last close
            data = None
            from backtest import load_dataset
            data = load_dataset(path, config)
            last_close = float(data["close"].iloc[-1])

            # We need to figure out if there was an open position.
            # The trade count and nature of the last trade sign gives us clues.
            # 
            # The strategy flips: long→short→long→short...
            # If total_trades is even, the last trade was closed (back to flat).
            # If total_trades is odd, there's an open position.
            total_trades = metrics.get("total_trades", 0)
            print(f"    Total trades (closed, in metrics): {total_trades}")
            
            if total_trades % 2 == 1:
                # There's an open position
                last_trade = trades_df.iloc[-1]
                last_side = last_trade["side"]
                last_entry_price = last_trade["entry_price"]
                if last_side == "LONG":
                    unrealized_pnl = last_close - last_entry_price
                else:
                    unrealized_pnl = last_entry_price - last_close
                
                print(f"    ▸ OPEN POSITION DETECTED: {last_side}")
                print(f"      Entry price: {last_entry_price:.6f}")
                print(f"      Last close:  {last_close:.6f}")
                print(f"      Unrealized PnL: {unrealized_pnl:.4f}")
                
                # Would including this change win rate?
                # If unrealized PnL > 0, the trade is a "win" if counted
                # If < 0, it's a "loss"
                if unrealized_pnl > 0:
                    print(f"      Including it would ADD 1 WIN (inflated win rate)")
                else:
                    print(f"      Including it would ADD 1 LOSS (potentially deflated win rate)")
                
                results.append({
                    "dataset": name,
                    "total_trades_closed": total_trades,
                    "open_position": True,
                    "open_side": last_side,
                    "unrealized_pnl": round(unrealized_pnl, 6),
                    "potential_win": unrealized_pnl > 0,
                })
                
                # Compute what the win rate would be if open trade is included
                current_wins = metrics.get("wins", 0)
                current_losses = metrics.get("losses", 0)
                adjusted_wins = current_wins + (1 if unrealized_pnl > 0 else 0)
                adjusted_losses = current_losses + (1 if unrealized_pnl <= 0 else 0)
                adjusted_total = adjusted_wins + adjusted_losses
                adjusted_wr = adjusted_wins / adjusted_total if adjusted_total > 0 else 0
                
                print(f"      Current win rate: {metrics.get('win_rate', 'N/A')}")
                print(f"      Adjusted win rate (with open trade): {adjusted_wr:.4f}")
            else:
                print(f"    ▸ No open position (even trade count, back to flat)")
                results.append({
                    "dataset": name,
                    "total_trades_closed": total_trades,
                    "open_position": False,
                    "open_side": None,
                    "unrealized_pnl": 0,
                    "potential_win": None,
                })
        else:
            print(f"\n  {name}: No trades")
            results.append({
                "dataset": name,
                "total_trades_closed": 0,
                "open_position": False,
                "open_side": None,
                "unrealized_pnl": 0,
                "potential_win": None,
            })

    # Save summary
    lines = []
    lines.append("=" * 70)
    lines.append("OPEN TRADE ANALYSIS RESULTS")
    lines.append("=" * 70)
    lines.append(f"\n{'Dataset':<20} {'Closed':<10} {'Open?':<10} {'Side':<8} {'UnrealPnL':<12} {'Would▲WR?':<12}")
    lines.append(f"{'─' * 70}")
    for r in results:
        open_str = "YES" if r["open_position"] else "no"
        side = str(r["open_side"] or "N/A")
        pnl = f"{r['unrealized_pnl']:.6f}" if r["open_position"] else "N/A"
        wr_impact = "WIN ▲" if r.get("potential_win") else ("LOSS ▼" if r["open_position"] else "N/A")
        lines.append(f"{r['dataset']:<20} {r['total_trades_closed']:<10} {open_str:<10} {side:<8} {pnl:<12} {wr_impact:<12}")

    lines.append(f"\n{'─' * 70}")
    lines.append("FINDINGS")
    lines.append(f"{'─' * 70}")
    
    open_count = sum(1 for r in results if r["open_position"])
    lines.append(f"  Datasets with open positions at end: {open_count}/{len(results)}")
    lines.append(f"  TradeSimulator.to_dataframe() only returns CLOSED trades.")
    lines.append(f"  Open positions are SILENTLY DROPPED from metrics.")
    lines.append(f"  This means metrics reflect only the subset of trades that completed.")
    
    # Impact analysis
    win_inflated = sum(1 for r in results if r.get("potential_win") == True)
    loss_hidden = sum(1 for r in results if r.get("potential_win") == False and r["open_position"])
    lines.append(f"\n  Inflated win rate risk (open win excluded): {win_inflated} datasets")
    lines.append(f"  Deflated win rate risk (open loss excluded): {loss_hidden} datasets")
    lines.append(f"\n  VERDICT: Open positions at end of dataset are silently excluded.")
    lines.append(f"  This is a common backtest pitfall. The magnitude of impact depends")
    lines.append(f"  on whether open trades are winners or losers.")

    report_text = "\n".join(lines)
    open_report_path = os.path.join(OUTPUT_DIR, "open_trade_analysis.txt")
    with open(open_report_path, "w") as f:
        f.write(report_text)
    print(f"\n  Saved: {open_report_path}")
    print(report_text)
    
    return results


# =============================================================================
# Item 3: New Metrics Design — Analyze EURUSD to provide example calculations
# =============================================================================

def analyze_new_metrics():
    """Analyze EURUSD trade data to provide example avg_win, avg_loss, expectancy."""
    print("\n" + "=" * 70)
    print("ITEM 3: NEW METRICS DESIGN — Avg Win / Avg Loss / Expectancy")
    print("=" * 70)

    config = BacktestConfig(bos_confirmation_window=10)
    strategy = BOSFlipStrategy()
    harness = BacktestHarness(config, strategy_callback=strategy)
    result = harness.run(EURUSD_PATH)

    trades_df = result.trades
    metrics = result.metrics

    print(f"\n  Current metrics for EURUSD-15M:")
    print(f"    Total trades: {metrics.get('total_trades', 'N/A')}")
    print(f"    Wins:         {metrics.get('wins', 'N/A')}")
    print(f"    Losses:       {metrics.get('losses', 'N/A')}")
    print(f"    Win rate:     {metrics.get('win_rate', 'N/A')}")
    print(f"    Gross profit: {metrics.get('gross_profit', 'N/A')}")
    print(f"    Gross loss:   {metrics.get('gross_loss', 'N/A')}")

    if trades_df is not None and not trades_df.empty:
        pnls = trades_df["pnl"].values
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]

        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.mean(abs(losses))) if len(losses) > 0 else 0.0
        win_rate = metrics["win_rate"]
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        print(f"\n  Computed new metrics:")
        print(f"    Avg win:          {avg_win:.6f}")
        print(f"    Avg loss:         {avg_loss:.6f}")
        print(f"    Expectancy:       {expectancy:.6f}")
        print(f"\n  Interpretation:")
        print(f"    Expectancy = {expectancy:.6f} pts per trade")
        print(f"    Win rate = {win_rate*100:.1f}%")
        print(f"    Avg win/loss ratio = {avg_win/avg_loss:.2f}x" if avg_loss > 0 else "    Avg win/loss ratio = N/A")

        # Detailed trade analysis
        print(f"\n  Trade PnL distribution:")
        print(f"    Min:      {float(np.min(pnls)):.6f}")
        print(f"    25th pct: {float(np.percentile(pnls, 25)):.6f}")
        print(f"    Median:   {float(np.median(pnls)):.6f}")
        print(f"    75th pct: {float(np.percentile(pnls, 75)):.6f}")
        print(f"    Max:      {float(np.max(pnls)):.6f}")
        print(f"    Std dev:  {float(np.std(pnls)):.6f}")

        return {
            "avg_win": round(avg_win, 6),
            "avg_loss": round(avg_loss, 6),
            "expectancy": round(expectancy, 6),
            "win_rate": win_rate,
            "avg_win_loss_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else float('inf'),
            "total_trades": metrics["total_trades"],
            "wins": metrics["wins"],
            "losses": metrics["losses"],
        }
    else:
        print("  No trades to analyze.")
        return None


# =============================================================================
# Item 4: event_id Design — Verify uniqueness in sample run
# =============================================================================

def analyze_event_id():
    """Simulate event_id by checking if events can be cross-referenced."""
    print("\n" + "=" * 70)
    print("ITEM 4: event_id DESIGN")
    print("=" * 70)

    config = BacktestConfig(bos_confirmation_window=10)
    strategy = BOSFlipStrategy()
    harness = BacktestHarness(config, strategy_callback=strategy)
    result = harness.run(EURUSD_PATH)

    structure_engine = result.structure_engine
    all_events = structure_engine.events
    confirmed_bos = [e for e in all_events if e.event_type == "BOS" and e.status == "confirmed"]
    cancelled = [e for e in all_events if e.event_type == "BOS" and e.status == "cancelled"]
    provisional = [e for e in all_events if e.event_type == "BOS" and e.status == "provisional"]

    print(f"\n  Total StructureEngine events: {len(all_events)}")
    print(f"  Confirmed BOS: {len(confirmed_bos)}")
    print(f"  Cancelled BOS: {len(cancelled)}")
    print(f"  Provisional BOS: {len(provisional)}")
    print(f"\n  Current event dedup: via (event_type, direction, swing_index) tuple key")
    print(f"  Problem: This key prevents duplicate events but doesn't provide")
    print(f"    a unique identifier for cross-referencing events between:")
    print(f"    - Streaming pipeline (StructureEvent)")
    print(f"    - Batch pipeline (report[\"BOS\"], report[\"BrokenIndex\"])")
    print(f"    - Trade log (which trade was triggered by which event?)")
    print(f"\n  Proposed: event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])")
    print(f"  This provides an 8-char hex ID per event instance.")

    # Count events — if we have 800+ events, 8-char hex has 16^8 = 4B space
    print(f"\n  Current event count: {len(all_events)}")
    print(f"  8-char hex ID space: 16^8 = 4,294,967,296")
    print(f"  Collision probability at {len(all_events)} events: negligible")
    print(f"\n  Backward compatibility: default value means existing consumers")
    print(f"    that don't use event_id will still work unchanged.")
    print(f"  The event_id field is appended to StructureEvent; no consumers")
    print(f"    need to be updated unless they want to USE the ID.")

    return {
        "total_events": len(all_events),
        "confirmed_bos": len(confirmed_bos),
        "cancelled": len(cancelled),
        "provisional": len(provisional),
    }


# =============================================================================
# Main
# =============================================================================

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    print(f"Output directory: {OUTPUT_DIR}")
    
    gap_df, missed_df, matched_df = analyze_gap()
    open_trade_results = analyze_open_trades()
    new_metrics = analyze_new_metrics()
    event_id_info = analyze_event_id()

    # ---- Summary across all items ----
    print("\n" + "=" * 70)
    print("ORACLE FORENSIC ANALYSIS — EXECUTIVE SUMMARY")
    print("=" * 70)

    print(f"\n  All outputs saved to: {OUTPUT_DIR}/")
    print(f"    - missed_vs_matched_comparison.csv")
    print(f"    - gap-analysis-report.txt")
    print(f"    - open_trade_analysis.txt")
    print(f"\n  Items completed: Gap Analysis, Open Trade Check,")
    print(f"    New Metrics Design, event_id Design")


if __name__ == "__main__":
    main()
