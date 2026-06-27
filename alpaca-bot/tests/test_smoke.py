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
    """Records orders. Notably has NO get_all_positions — the sleeve must
    never read account-wide positions, so touching it would raise."""
    def __init__(self):
        self.orders = []

    def submit_order(self, req):
        self.orders.append(req)


def test_sleeve_budget_and_orders():
    pairs = {"BTC/USD": "btc", "ETH/USD": "eth"}
    last_px = pd.Series({"btc": 100.0, "eth": 50.0})
    # fresh $10k sleeve, all cash
    s = live.Sleeve(cash=10_000.0, peak=10_000.0)
    assert s.equity(last_px) == 10_000.0
    w_tgt = pd.Series({"btc": 0.30, "eth": 0.20})
    deltas = live.plan_orders(s, pairs, w_tgt, last_px)
    t = FakeTrading()
    live.execute(t, s, pairs, deltas, last_px)
    assert {o.symbol for o in t.orders} == {"BTC/USD", "ETH/USD"}
    # bought $3000 btc + $2000 eth, sized to the $10k sleeve, not more
    assert abs(s.holdings["btc"] - 30.0) < 1e-9   # $3000 / $100
    assert abs(s.holdings["eth"] - 40.0) < 1e-9   # $2000 / $50
    # cash fell by the notional plus fees; equity stays ~ $10k (minus fees)
    assert s.cash < 5_000.0
    assert 9_980.0 < s.equity(last_px) <= 10_000.0


def test_sleeve_round_trip_keeps_budget():
    """Two runs in a row shouldn't conjure or destroy capital beyond fees."""
    pairs = {"BTC/USD": "btc"}
    s = live.Sleeve(cash=10_000.0, peak=10_000.0)
    px1 = pd.Series({"btc": 100.0})
    live.execute(FakeTrading(), s, pairs,
                 live.plan_orders(s, pairs, pd.Series({"btc": 0.5}), px1), px1)
    eq_after_buy = s.equity(px1)
    # price doubles; sell it all back to cash
    px2 = pd.Series({"btc": 200.0})
    eq_before_sell = s.equity(px2)
    live.execute(FakeTrading(), s, pairs,
                 live.plan_orders(s, pairs, pd.Series({"btc": 0.0}), px2), px2)
    assert s.holdings.get("btc", 0.0) == 0.0          # fully exited
    assert abs(s.equity(px2) - s.cash) < 1e-9         # all cash now
    assert eq_after_buy < eq_before_sell              # the coin appreciated
    assert s.cash > eq_after_buy                       # profit captured (net of fees)
    assert s.cash > 14_000.0                           # ~ doubled the 50% held


def test_sleeve_skips_dust():
    pairs = {"BTC/USD": "btc"}
    last_px = pd.Series({"btc": 100.0})
    s = live.Sleeve(cash=5_000.0, holdings={"btc": 50.0}, peak=10_000.0)
    # target 50% of $10k = $5000, already holding $5000 -> no trade
    deltas = live.plan_orders(s, pairs, pd.Series({"btc": 0.50}), last_px)
    assert deltas.empty


def test_equity_log_one_row_per_date(tmp_path="/tmp/claude_eqlog_test.csv"):
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    px = pd.Series({"btc": 100.0})
    s = live.Sleeve(cash=10_000.0, peak=10_000.0)
    live.append_log("2026-06-26", s, px, 0.0, 1.0, path=tmp_path)
    s2 = live.Sleeve(cash=5_000.0, holdings={"btc": 50.0}, peak=10_000.0)
    live.append_log("2026-06-27", s2, px, 0.0, 1.0, path=tmp_path)
    # re-logging the same date overwrites, not appends
    live.append_log("2026-06-27", s2, px, 0.05, 0.5, path=tmp_path)
    import csv as _csv
    with open(tmp_path) as f:
        rows = list(_csv.DictReader(f))
    assert [r["date"] for r in rows] == ["2026-06-26", "2026-06-27"]
    assert rows[0]["positions"] == "cash"
    assert rows[1]["positions"] == "btc:0.500"   # $5000 of $10000
    assert rows[1]["invested"] == "5000.00"
    assert rows[1]["breaker_scale"] == "0.50"    # latest write wins
    os.remove(tmp_path)


if __name__ == "__main__":
    for fn in [test_weights_sane, test_breaker_recovers,
               test_sleeve_budget_and_orders, test_sleeve_round_trip_keeps_budget,
               test_sleeve_skips_dust, test_equity_log_one_row_per_date]:
        fn()
        print(f"ok {fn.__name__}")
