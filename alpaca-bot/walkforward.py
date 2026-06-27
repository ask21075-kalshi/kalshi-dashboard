"""Walk-forward test: each January, pick the candidate config with the best
risk-adjusted score on the trailing 3 years, then trade it for that year.
No year's own data is ever used to pick its config.
"""
import pandas as pd

from bot import backtest
from bot.data import load_prices
from bot.strategy import Params

CANDIDATES = {
    "fast":   Params(mom_windows=(7, 30, 90), mom_weights=(1/3,) * 3,
                     ema_window=50, regime_ema=100, top_k=3, target_vol=0.6),
    "mid":    Params(mom_windows=(14, 30, 90), mom_weights=(1/3,) * 3,
                     ema_window=50, regime_ema=100, top_k=3, target_vol=0.6),
    "slow":   Params(mom_windows=(30, 90, 180), mom_weights=(1/3,) * 3,
                     ema_window=100, regime_ema=150, top_k=3, target_vol=0.6),
    "slow-conserv": Params(mom_windows=(30, 90, 180), mom_weights=(1/3,) * 3,
                           ema_window=100, regime_ema=150, top_k=2,
                           target_vol=0.4, dd_soft=0.15, dd_hard=0.25),
    "pure90": Params(mom_windows=(90,), mom_weights=(1.0,),
                     ema_window=100, regime_ema=200, top_k=2, target_vol=0.6),
    "aggressive": Params(mom_windows=(14, 30, 90), mom_weights=(1/3,) * 3,
                         ema_window=50, regime_ema=100, top_k=4,
                         target_vol=0.8, vol_window=30),
}


def score(r):
    return r.cagr / (r.max_dd + 0.10)


def main():
    px = load_prices()
    last = px.index[-1]
    rows = []
    equity = 10_000.0
    curve = []
    for year in range(2018, last.year + 1):
        sel_start, sel_end = f"{year - 3}-01-01", f"{year - 1}-12-31"
        scores = {}
        for name, p in CANDIDATES.items():
            r = backtest.run(px, p, start=sel_start, end=sel_end)
            scores[name] = score(r)
        pick = max(scores, key=scores.get)
        r = backtest.run(px, CANDIDATES[pick], start=f"{year}-01-01",
                         end=f"{year}-12-31", capital=equity)
        yr_ret = r.equity.iloc[-1] / equity - 1.0
        equity = float(r.equity.iloc[-1])
        curve.append(r.equity)
        rows.append((year, pick, yr_ret, r.max_dd, equity))
        print(f"{year}: pick={pick:13s} ret={yr_ret:+7.1%} "
              f"maxDD={r.max_dd:5.1%} equity=${equity:>10,.0f}")

    eq = pd.concat(curve)
    n_years = len(eq) / 365.0
    cagr = (equity / 10_000) ** (1 / n_years) - 1
    dd = float((1 - eq / eq.cummax()).max())
    daily = eq.pct_change().dropna()
    sharpe = float(daily.mean() / daily.std() * (365 ** 0.5))
    print(f"\nWALK-FORWARD {eq.index[0].date()} -> {eq.index[-1].date()}: "
          f"CAGR {cagr:+.1%} | MaxDD {dd:.1%} | Sharpe {sharpe:.2f} | "
          f"${equity:,.0f} from $10k")


if __name__ == "__main__":
    main()
