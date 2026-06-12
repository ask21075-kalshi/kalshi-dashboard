"""Momentum rotation strategy.

Shared by the backtester and the live Alpaca runner so what we test is what
we trade.

Logic, evaluated once per day on daily closes:
  1. Momentum score per asset: blend of multi-window returns.
  2. Eligibility: price above its EMA and positive momentum score.
  3. Hold the top `top_k` eligible assets, inverse-volatility weighted.
     Rank hysteresis: an asset already held is kept while it stays in the
     top `hold_k` (>= top_k) and eligible, which cuts churn.
  4. Volatility targeting: scale gross exposure so recent portfolio vol
     matches `target_vol` (annualized), capped at 1.0 (no leverage).
  5. Rebalance band: per-asset weight changes smaller than `band` are
     skipped to save fees.
  6. Circuit breaker: when strategy equity is in a drawdown beyond
     `dd_soft`, halve exposure; beyond `dd_hard`, go fully to cash for
     `dd_cooldown` days, then reset the equity peak and resume.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Params:
    mom_windows: tuple = (14, 30, 90)
    mom_weights: tuple = (0.4, 0.3, 0.3)
    ema_window: int = 50
    regime_asset: str = "btc"  # market filter: flat when this is below its EMA
    regime_ema: int = 100      # 0 disables the regime filter
    top_k: int = 3
    hold_k: int = 5            # keep a held asset while ranked in top hold_k
    min_score: float = 0.0     # momentum score needed to enter a position
    vol_window: int = 20
    target_vol: float = 0.60   # annualized
    band: float = 0.05         # skip per-asset trades smaller than this weight
    dd_soft: float = 0.20
    dd_hard: float = 0.30
    dd_cooldown: int = 10      # days flat after a hard-breaker trip
    min_history: int = 100     # days of data an asset needs before it's tradable


@dataclass
class Breaker:
    """Drawdown circuit breaker state. Feed it equity once per day."""
    peak: float = 0.0
    halt_days_left: int = 0
    drawdown: float = field(default=0.0, init=False)

    def update(self, equity: float, p: Params) -> float:
        """Returns an exposure multiplier in {0.0, 0.5, 1.0}."""
        if self.halt_days_left > 0:
            self.halt_days_left -= 1
            if self.halt_days_left == 0:
                self.peak = equity  # reset peak, resume fresh
            self.drawdown = 0.0
            return 0.0
        self.peak = max(self.peak, equity)
        self.drawdown = 1.0 - equity / self.peak if self.peak > 0 else 0.0
        if self.drawdown >= p.dd_hard:
            self.halt_days_left = p.dd_cooldown
            return 0.0
        if self.drawdown >= p.dd_soft:
            return 0.5
        return 1.0


@dataclass
class Signals:
    """Per-day signal matrices, same index/columns as the price frame."""
    score: pd.DataFrame     # blended momentum score
    rank: pd.DataFrame      # rank of score among eligible (1 = best), NaN if not
    eligible: pd.DataFrame  # bool: tradable + trend + positive momentum
    vol: pd.DataFrame       # annualized realized vol
    risk_on: pd.Series      # bool: market regime allows positions


def compute_signals(prices: pd.DataFrame, p: Params) -> Signals:
    score = sum(
        wt * prices.pct_change(w) for w, wt in zip(p.mom_windows, p.mom_weights)
    )
    ema = prices.ewm(span=p.ema_window).mean()
    valid = prices.notna().rolling(p.min_history).sum() >= p.min_history
    eligible = (score > p.min_score) & (prices > ema) & valid
    rank = score.where(eligible).rank(axis=1, ascending=False)
    vol = prices.pct_change().rolling(p.vol_window).std() * np.sqrt(365)
    if p.regime_ema and p.regime_asset in prices.columns:
        ra = prices[p.regime_asset]
        risk_on = ra > ra.ewm(span=p.regime_ema).mean()
    else:
        risk_on = pd.Series(True, index=prices.index)
    return Signals(score, rank, eligible, vol, risk_on)


def select_picks(rank_row: pd.Series, held: list, p: Params) -> list:
    """Top-k selection with rank hysteresis."""
    keep = [a for a in held if rank_row.get(a, np.nan) <= p.hold_k]
    new = [a for a in rank_row.dropna().sort_values().index
           if a not in keep]
    return keep + new[: max(0, p.top_k - len(keep))]


def target_weights(sig: Signals, day, w_prev: pd.Series, p: Params,
                   breaker_scale: float = 1.0,
                   rets_window: pd.DataFrame = None) -> pd.Series:
    """Target weights for `day`, given signals and current weights.

    `rets_window` is the trailing daily-returns window ending at `day`
    (used for portfolio vol); if None, per-asset vols are combined
    ignoring diversification.
    """
    weights = pd.Series(0.0, index=sig.score.columns)
    if not bool(sig.risk_on.loc[day]):
        return weights
    rank_row = sig.rank.loc[day]
    held = list(w_prev[w_prev > 0].index)
    picks = select_picks(rank_row, held, p)
    if not picks:
        return weights

    vol_row = sig.vol.loc[day, picks]
    inv = (1.0 / vol_row.clip(lower=0.10)).fillna(0.0)
    if inv.sum() == 0:
        return weights
    w = inv / inv.sum()

    if rets_window is not None:
        port_vol = float((rets_window[picks] @ w).std() * np.sqrt(365))
    else:
        port_vol = float((vol_row * w).sum())  # ignores diversification
    scale = min(1.0, p.target_vol / port_vol) if port_vol > 0 else 0.0

    weights[picks] = w * scale * breaker_scale

    # rebalance band: keep previous weight where the change is small
    # (only for assets we still want to hold — full exits always execute)
    small = ((weights - w_prev).abs() < p.band) & (weights > 0)
    weights[small] = w_prev[small]
    if breaker_scale == 0.0:
        weights[:] = 0.0
    return weights
