#!/usr/bin/env python3
"""
Item 3: New Metrics — avg_win, avg_loss, expectancy.
Compute from EURUSD data with current streaming pipeline.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backtest import BacktestConfig, BacktestHarness
from strategies.bos_flip import BOSFlipStrategy

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
EURUSD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..",
    "tests/test_data/EURUSD/EURUSD_15M.csv"
)

def run():
    print("=" * 70)
    print("ITEM 3: NEW METRICS — Avg Win / Avg Loss / Expectancy")
    print("=" * 70)

    config = BacktestConfig(bos_confirmation_window=10)
    strategy = BOSFlipStrategy()
    harness = BacktestHarness(config, strategy_callback=strategy)
    result = harness.run(EURUSD_PATH)

    trades_df = result.trades
    metrics = result.metrics

    print(f"\n  Dataset: EURUSD-15M")
    print(f"  Trade count: {metrics['total_trades']}")
    print(f"  Current win rate: {metrics['win_rate']:.4f}")

    if trades_df is None or trades_df.empty:
        print("  No trades to analyze.")
        return

    pnls = trades_df["pnl"].values
    wins_arr = pnls[pnls > 0]
    losses_arr = pnls[pnls <= 0]

    total = len(pnls)
    wins = len(wins_arr)
    losses = len(losses_arr)
    win_rate = wins / total if total > 0 else 0.0

    avg_win = float(np.mean(wins_arr)) if wins > 0 else 0.0
    avg_loss = float(np.mean(abs(losses_arr))) if losses > 0 else 0.0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    profit_factor = metrics["gross_profit"] / metrics["gross_loss"] if metrics["gross_loss"] > 0 else float("inf")

    print(f"\n  {'─' * 50}")
    print(f"  {'New Metric':<20} {'Value':<20} {'Formula'}")
    print(f"  {'─' * 50}")
    print(f"  {'avg_win':<20} {avg_win:<20.6f} sum(wins) / {wins} winning trades")
    print(f"  {'avg_loss':<20} {avg_loss:<20.6f} sum(|losses|) / {losses} losing trades")
    print(f"  {'expectancy':<20} {expectancy:<20.6f} WR * avg_win - (1-WR) * avg_loss")
    print(f"  {'─' * 50}")
    print(f"  Reference metrics:")
    print(f"    win_rate:        {win_rate:.4f} ({win_rate*100:.2f}%)")
    print(f"    gross_profit:    {metrics['gross_profit']:.4f}")
    print(f"    gross_loss:      {metrics['gross_loss']:.4f}")
    print(f"    profit_factor:   {profit_factor:.4f}")
    print(f"    net_pnl:         {metrics['net_pnl']:.4f}")
    print(f"\n  Avg win / avg loss ratio: {avg_win/avg_loss:.2f}x" if avg_loss > 0 else "")
    print(f"\n  Interpretation:")
    print(f"    Each trade is expected to yield {expectancy:.6f} pips.")
    print(f"    Over {total} trades: {expectancy * total:.4f} total expected (actual: {metrics['net_pnl']:.4f})")
    print(f"    The strategy wins {win_rate*100:.1f}% of the time,")
    print(f"    but when it loses, the avg loss ({avg_loss:.6f}) is")
    if avg_win > avg_loss:
        print(f"    smaller than the avg win ({avg_win:.6f}). (R:R > 1)")
    else:
        print(f"    larger than the avg win ({avg_win:.6f}). (R:R < 1)")

    # Trade PnL distribution
    print(f"\n  Trade PnL distribution:")
    pctiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    for p in pctiles:
        print(f"    {p:2d}th percentile: {np.percentile(pnls, p):.6f}")
    print(f"    Min:              {float(np.min(pnls)):.6f}")
    print(f"    Max:              {float(np.max(pnls)):.6f}")
    print(f"    Std dev:          {float(np.std(pnls)):.6f}")

    # Cross-market analysis for all datasets
    print(f"\n{'=' * 70}")
    print("CROSS-MARKET NEW METRICS")
    print(f"{'=' * 70}")

    datasets = [
        {"path": "tests/test_data/cryptocurrencies/binance_api_BTCUSDT_4h.csv", "name": "BTCUSDT-4H", "dc": "time", "df": None},
        {"path": "tests/test_data/cryptocurrencies/binance_api_SOLUSDT_4h.csv", "name": "SOLUSDT-4H", "dc": "time", "df": None},
        {"path": "tests/test_data/cryptocurrencies/binance_api_ADAUSDT_4h.csv", "name": "ADAUSDT-4H", "dc": "time", "df": None},
        {"path": "tests/test_data/cryptocurrencies/binance_api_BNBUSDT_4h.csv", "name": "BNBUSDT-4H", "dc": "time", "df": None},
        {"path": "tests/test_data/EURUSD/EURUSD_15M.csv", "name": "EURUSD-15M", "dc": "Date", "df": "%Y.%m.%d %H:%M:%S"},
    ]
    
    all_metrics = {}
    for ds in datasets:
        full_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ds["path"]
        )
        if not os.path.exists(full_path):
            print(f"\n  {ds['name']}: SKIP")
            continue
        
        cfg = BacktestConfig(date_column=ds["dc"], date_format=ds["df"])
        strat = BOSFlipStrategy()
        h = BacktestHarness(cfg, strategy_callback=strat)
        r = h.run(full_path)
        
        tdf = r.trades
        m = r.metrics
        
        if tdf is None or tdf.empty:
            print(f"\n  {ds['name']}: No trades")
            continue
        
        p = tdf["pnl"].values
        w_arr = p[p > 0]
        l_arr = p[p <= 0]
        t = len(p)
        wt = len(w_arr)
        lt = len(l_arr)
        wr = wt / t if t > 0 else 0
        aw = float(np.mean(w_arr)) if wt > 0 else 0
        al = float(np.mean(abs(l_arr))) if lt > 0 else 0
        exp = (wr * aw) - ((1 - wr) * al)

        all_metrics[ds["name"]] = {
            "total_trades": t,
            "wins": wt,
            "losses": lt,
            "win_rate": round(wr, 4),
            "avg_win": round(aw, 6),
            "avg_loss": round(al, 6),
            "expectancy": round(exp, 6),
            "profit_factor": m["profit_factor"],
        }

    print(f"\n  {'Dataset':<15} {'Trades':<8} {'WR':<8} {'AvgWin':<12} {'AvgLoss':<12} {'Expect':<12} {'PF':<8}")
    print(f"  {'─' * 75}")
    for name, m in all_metrics.items():
        print(f"  {name:<15} {m['total_trades']:<8} {m['win_rate']:<8.4f} {m['avg_win']:<12.6f} {m['avg_loss']:<12.6f} {m['expectancy']:<12.6f} {m['profit_factor']:<8.4f}")

    # Save
    import json
    json_path = os.path.join(OUTPUT_DIR, "new_metrics_crossmarket.json")
    with open(json_path, "w") as f:
        json.dump(all_metrics, f, indent=2)
    print(f"\n  Saved: {json_path}")


if __name__ == "__main__":
    run()
