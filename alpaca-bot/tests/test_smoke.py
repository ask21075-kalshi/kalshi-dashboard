"""Offline smoke tests: strategy math and live order generation."""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bot import live  # noqa: E402
from bot.strategy import (Breaker, Params, compute_signals,  # noqa: E402
                          target_weights)


def fake_prices(days=300, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=days, freq="D")
    cols = {}
    for i, name in enumerate(["btc", "eth", "sol", "doge"]):
        drift = 0.002 - 0.001 * i
        cols[name] = 100 * np.exp(
            np.cumsum(rng.normal(drift, 0.03, days)))
    return pd.DataFrame(cols, index=idx)


def test_weights_sane():
    px = fake_prices()
    p = Params()
    sig = compute_signals(px, p)
    w0 = pd.Series(0.0, index=px.columns)
    w = target_weights(sig, px.index[-1], w0, p,
                       rets_window=px.pct_change().iloc[-p.vol_window:])
    assert (w >= 0).all() and w.sum() <= 1.0 + 1e-9
    # breaker fully off -> all cash
    w_halt = target_weights(sig, px.index[-1], w0, p, breaker_scale=0.0)
    assert w_halt.sum() == 0.0


def test_breaker_recovers():
    p = Params(dd_cooldown=3)
    b = Breaker(peak=100.0)
    assert b.update(100.0, p) == 1.0
    assert b.update(75.0, p) == 0.5      # past dd_soft
    assert b.update(65.0, p) == 0.0      # past dd_hard -> halt
    assert b.update(65.0, p) == 0.0      # cooldown day 1
    assert b.update(65.0, p) == 0.0      # cooldown day 2
    assert b.update(65.0, p) == 0.0      # cooldown day 3, peak resets
    assert b.update(66.0, p) == 1.0      # back to normal at new peak


class FakeTrading:
    def __init__(self):
        self.orders = []

    def submit_order(self, req):
        self.orders.append(req)


def test_rebalance_orders():
    pairs = {"BTC/USD": "btc", "ETH/USD": "eth"}
    w_now = pd.Series({"btc": 0.50, "eth": 0.00})
    w_tgt = pd.Series({"btc": 0.20, "eth": 0.40})
    t = FakeTrading()
    live.rebalance(t, pairs, w_now, w_tgt, equity=10_000)
    assert len(t.orders) == 2
    sell, buy = t.orders  # sells must come first
    assert sell.symbol == "BTC/USD" and float(sell.notional) == 3000.0
    assert sell.side.value == "sell"
    assert buy.symbol == "ETH/USD" and float(buy.notional) == 4000.0
    assert buy.side.value == "buy"


def test_rebalance_skips_dust():
    pairs = {"BTC/USD": "btc"}
    t = FakeTrading()
    live.rebalance(t, pairs, pd.Series({"btc": 0.5000}),
                   pd.Series({"btc": 0.5004}), equity=10_000)
    assert t.orders == []


if __name__ == "__main__":
    for fn in [test_weights_sane, test_breaker_recovers,
               test_rebalance_orders, test_rebalance_skips_dust]:
        fn()
        print(f"ok {fn.__name__}")
