#!/usr/bin/env python3
"""
Item 2 Corrected: Open Trade Analysis.
The BOSFlipStrategy always flips direction — so every signal creates a new position.
There is ALWAYS an open position at end of dataset (unless zero signals).
TradeSimulator.to_dataframe() only returns CLOSED trades, silently dropping the open one.
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backtest import BacktestConfig, BacktestHarness, load_dataset
from strategies.bos_flip import BOSFlipStrategy

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")

DATASETS = [
    {"path": "tests/test_data/cryptocurrencies/binance_api_BTCUSDT_4h.csv", "name": "BTCUSDT-4H", "date_column": "time", "date_format": None},
    {"path": "tests/test_data/cryptocurrencies/binance_api_SOLUSDT_4h.csv", "name": "SOLUSDT-4H", "date_column": "time", "date_format": None},
    {"path": "tests/test_data/cryptocurrencies/binance_api_ADAUSDT_4h.csv", "name": "ADAUSDT-4H", "date_column": "time", "date_format": None},
    {"path": "tests/test_data/cryptocurrencies/binance_api_BNBUSDT_4h.csv", "name": "BNBUSDT-4H", "date_column": "time", "date_format": None},
    {"path": "tests/test_data/EURUSD/EURUSD_15M.csv", "name": "EURUSD-15M", "date_column": "Date", "date_format": "%Y.%m.%d %H:%M:%S"},
]

def run():
    print("=" * 70)
    print("ITEM 2: OPEN TRADE ANALYSIS (CORRECTED)")
    print("=" * 70)

    # Load baseline metrics for comparison
    baseline_path = os.path.join(PROJECT_ROOT, ".sisyphus/evidence/bosflip-crossmarket/metrics.json")
    with open(baseline_path) as f:
        baseline = json.load(f)

    all_results = []

    for ds in DATASETS:
        name = ds["name"]
        path = os.path.join(PROJECT_ROOT, ds["path"])
        if not os.path.exists(path):
            print(f"\n  {name}: SKIP (file not found)")
            continue

        config = BacktestConfig(date_column=ds["date_column"], date_format=ds["date_format"])
        strategy = BOSFlipStrategy()
        harness = BacktestHarness(config, strategy_callback=strategy)
        result = harness.run(path)

        trades_df = result.trades
        metrics = result.metrics
        data = load_dataset(path, config)

        total_trades = metrics.get("total_trades", 0)
        wins = metrics.get("wins", 0)
        losses = metrics.get("losses", 0)
        win_rate = metrics.get("win_rate", float("nan"))
        baseline_trades = baseline.get(name, {}).get("total_trades", "N/A")

        last_close = float(data["close"].iloc[-1])

        # Detect open position from TradeSimulator behavior:
        # The strategy always enters after closing, so open position exists iff
        # at least one signal occurred.
        open_position_exists = total_trades > 0
        open_side = None
        open_entry_price = None
        unrealized_pnl = None
        trade_count_penalty = None

        if open_position_exists and trades_df is not None and not trades_df.empty:
            last_trade = trades_df.iloc[-1]
            last_trade_side = last_trade["side"]
            # The open position flips from the last closed trade
            open_side = "SHORT" if last_trade_side == "LONG" else "LONG"
            open_entry_price = float(last_trade["exit_price"])
            
            if open_side == "LONG":
                unrealized_pnl = last_close - open_entry_price
            else:
                unrealized_pnl = open_entry_price - last_close

            # What the metrics would be if this trade were included
            if unrealized_pnl > 0:
                trade_count_penalty = f"Would ADD 1 win (WR → {(wins+1)/(total_trades+1)*100:.2f}%)"
            else:
                trade_count_penalty = f"Would ADD 1 loss (WR → {wins/(total_trades+1)*100:.2f}%)"

        print(f"\n  {name}:")
        print(f"    Baseline trades (batch): {baseline_trades}")
        print(f"    Current trades (streaming): {total_trades}")
        print(f"    Wins/Losses: {wins}/{losses}")
        print(f"    Win rate: {win_rate}")
        if open_position_exists:
            print(f"    ▸ OPEN POSITION at end: {open_side}")
            print(f"      Entry price ≈ {open_entry_price:.6f}")
            print(f"      Last close:  {last_close:.6f}")
            print(f"      Unrealized PnL: {unrealized_pnl:.6f}")
            print(f"      Impact: {trade_count_penalty}")
        else:
            print(f"    ▸ No trades — flat")

        all_results.append({
            "dataset": name,
            "baseline_trades": baseline_trades,
            "current_trades": total_trades,
            "open_position": open_side if open_position_exists else None,
            "unrealized_pnl": round(unrealized_pnl, 6) if unrealized_pnl is not None else 0,
            "impact": trade_count_penalty or "N/A",
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
        })

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY: Open Trade Impact")
    print(f"{'=' * 70}")
    print(f"\n{'Dataset':<18} {'Trades':<8} {'Open?':<10} {'UnrealPnL':<14} {'Impact':<25}")
    print(f"{'─' * 75}")
    for r in all_results:
        open_str = r["open_position"] or "flat"
        pnl = f"{r['unrealized_pnl']:.6f}" if r['unrealized_pnl'] != 0 else "N/A"
        print(f"{r['dataset']:<18} {r['current_trades']:<8} {open_str:<10} {pnl:<14} {r['impact']:<25}")
    
    print(f"\n{'─' * 75}")
    print("KEY FINDING: TradeSimulator.to_dataframe() only returns CLOSED trades.")
    print("The open position at dataset end is SILENTLY DROPPED from all metrics.")
    print("With BOSFlipStrategy (always flips), there is ALWAYS an open position")
    print("after the first signal. This means metrics consistently miss 1 trade.")
    print(f"\n  Datasets with open positions: {sum(1 for r in all_results if r['open_position'] is not None)}/{len(all_results)}")
    
    winners = [r for r in all_results if r.get('unrealized_pnl', 0) > 0]
    losers = [r for r in all_results if r.get('unrealized_pnl', 0) < 0]
    print(f"  Open trades that are winners: {len(winners)}")
    print(f"  Open trades that are losers:  {len(losers)}")
    
    # Win rate impact summary
    print(f"\n  Win rate IMPACT:")
    for r in all_results:
        if r['open_position']:
            w, t = r['wins'], r['current_trades']
            current_wr = w / t if t > 0 else 0
            adjusted_w = w + (1 if r['unrealized_pnl'] > 0 else 0)
            adjusted_t = t + 1
            adjusted_wr = adjusted_w / adjusted_t
            delta = adjusted_wr - current_wr
            print(f"    {r['dataset']}: {current_wr*100:.2f}% → {adjusted_wr*100:.2f}% (Δ={delta*100:+.2f}pp)")

if __name__ == "__main__":
    run()
