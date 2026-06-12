"""Daily backtest engine for the momentum rotation strategy.

Signals are computed on day t's close and the resulting trades are filled
at day t+1's close (no look-ahead). Costs model Alpaca crypto taker fees
plus slippage.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategy import Breaker, Params, compute_signals, target_weights

FEE = 0.0025       # Alpaca crypto taker fee (worst tier)
SLIPPAGE = 0.0010  # spread/impact estimate per side
COST = FEE + SLIPPAGE


@dataclass
class Result:
    equity: pd.Series
    weights: pd.DataFrame
    cagr: float
    max_dd: float
    sharpe: float
    yearly: pd.Series
    turnover: float  # avg daily one-sided turnover, fraction of equity

    def summary(self) -> str:
        years = self.yearly.map(lambda r: f"{r:+.0%}")
        return (
            f"CAGR {self.cagr:+.1%} | MaxDD {self.max_dd:.1%} | "
            f"Sharpe {self.sharpe:.2f} | Turnover {self.turnover:.1%}/day\n"
            + " ".join(f"{y}:{v}" for y, v in years.items())
        )


def run(prices: pd.DataFrame, p: Params, start: str = None, end: str = None,
        capital: float = 10_000.0, cost: float = COST) -> Result:
    prices = prices.loc[: prices.last_valid_index()]
    if end is not None:
        prices = prices.loc[:end]
    sig = compute_signals(prices, p)
    rets = prices.pct_change()
    idx = prices.index
    start_i = max(p.min_history, max(p.mom_windows) + 1)
    if start is not None:
        start_i = max(start_i, int(idx.searchsorted(pd.Timestamp(start))))

    equity = capital
    w_prev = pd.Series(0.0, index=prices.columns)
    breaker = Breaker(peak=capital)
    eq_hist, w_hist, trades = [], [], []

    for i in range(start_i, len(idx)):
        day = idx[i]
        # day i: weights decided on day i-1's close earn day-i returns,
        # using each asset's actual return (positions drift, but daily
        # rebalancing makes weight drift a second-order effect)
        day_ret = float((w_prev * rets.iloc[i]).fillna(0.0).sum())
        equity *= 1.0 + day_ret

        # signal on day i's close -> rebalance at day i's close
        scale = breaker.update(equity, p)
        w_new = target_weights(
            sig, day, w_prev, p, breaker_scale=scale,
            rets_window=rets.iloc[max(0, i - p.vol_window + 1): i + 1])
        trade = float((w_new - w_prev).abs().sum())
        equity *= 1.0 - trade * cost

        eq_hist.append(equity)
        w_hist.append(w_new)
        trades.append(trade)
        w_prev = w_new

    eq = pd.Series(eq_hist, index=idx[start_i:], name="equity")
    daily = eq.pct_change().dropna()
    n_years = len(eq) / 365.0
    cagr = (eq.iloc[-1] / capital) ** (1.0 / n_years) - 1.0
    max_dd = float((1.0 - eq / eq.cummax()).max())
    sharpe = float(daily.mean() / daily.std() * np.sqrt(365)) if daily.std() > 0 else 0.0
    yearly = eq.groupby(eq.index.year).apply(lambda s: s.iloc[-1] / s.iloc[0] - 1.0)
    return Result(eq, pd.DataFrame(w_hist, index=eq.index), cagr, max_dd,
                  sharpe, yearly, float(np.mean(trades) / 2.0))
