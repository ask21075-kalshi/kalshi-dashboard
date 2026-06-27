"""Backtest the strategy on Coin Metrics daily data.

Usage:
    python run_backtest.py [--start 2018-01-01] [--end YYYY-MM-DD]
                           [--capital 10000] [--refresh]
"""
import argparse

from bot import backtest
from bot.data import load_prices
from bot.params import load_params


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=10_000)
    ap.add_argument("--refresh", action="store_true",
                    help="re-download price data")
    args = ap.parse_args()

    px = load_prices(refresh=args.refresh)
    p = load_params()
    print("params:", p)
    r = backtest.run(px, p, start=args.start, end=args.end,
                     capital=args.capital)
    print(r.summary())
    print(f"final equity: ${r.equity.iloc[-1]:,.0f} "
          f"(from ${args.capital:,.0f} on {r.equity.index[0].date()})")


if __name__ == "__main__":
    main()
