"""Live (paper) trading runner for Alpaca.

Runs the same strategy code as the backtest, once per day:
  1. Pull ~1 year of daily crypto bars from Alpaca's data API.
  2. Compute target weights (momentum signals + breaker on account equity).
  3. Rebalance with notional market orders (sells first, then buys).

Breaker state persists in state.json next to this package. Keys are read
from the environment: ALPACA_API_KEY, ALPACA_SECRET_KEY.
"""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pandas as pd

from .data import UNIVERSE
from .strategy import Breaker, Params, compute_signals, target_weights

log = logging.getLogger("bot")

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state.json")
MIN_ORDER_USD = 10.0


def make_clients():
    from alpaca.data.historical import CryptoHistoricalDataClient
    from alpaca.trading.client import TradingClient

    key = os.environ["ALPACA_API_KEY"]
    secret = os.environ["ALPACA_SECRET_KEY"]
    return (TradingClient(key, secret, paper=True),
            CryptoHistoricalDataClient(key, secret))


def tradable_universe(trading) -> dict:
    """Alpaca symbol -> our asset id, for pairs actually tradable today."""
    from alpaca.trading.enums import AssetClass
    from alpaca.trading.requests import GetAssetsRequest

    assets = trading.get_all_assets(
        GetAssetsRequest(asset_class=AssetClass.CRYPTO))
    ok = {a.symbol for a in assets if a.tradable}
    pairs = {pair: cid for cid, pair in UNIVERSE.items() if pair in ok}
    skipped = set(UNIVERSE.values()) - set(pairs)
    if skipped:
        log.info("not tradable on Alpaca, skipping: %s", sorted(skipped))
    return pairs


def fetch_daily_closes(data_client, pairs: dict, days: int = 400) -> pd.DataFrame:
    from alpaca.data.requests import CryptoBarsRequest
    from alpaca.data.timeframe import TimeFrame

    req = CryptoBarsRequest(
        symbol_or_symbols=list(pairs),
        timeframe=TimeFrame.Day,
        start=datetime.now(timezone.utc) - timedelta(days=days),
    )
    bars = data_client.get_crypto_bars(req).df
    closes = bars["close"].unstack(level="symbol")
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    closes = closes[~closes.index.duplicated(keep="last")]
    return closes.rename(columns=pairs)  # columns -> asset ids (btc, eth, ...)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def current_weights(trading, pairs: dict, equity: float) -> pd.Series:
    w = pd.Series(0.0, index=list(pairs.values()))
    for pos in trading.get_all_positions():
        sym = pos.symbol  # e.g. "BTCUSD"
        for pair, cid in pairs.items():
            if pair.replace("/", "") == sym:
                w[cid] = float(pos.market_value) / equity
    return w


def rebalance(trading, pairs: dict, w_now: pd.Series, w_tgt: pd.Series,
              equity: float, dry_run: bool = False):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    inv = {cid: pair for pair, cid in pairs.items()}
    deltas = ((w_tgt - w_now) * equity).round(2)
    deltas = deltas[deltas.abs() >= MIN_ORDER_USD]
    # sells first to free up cash for the buys
    for cid, usd in sorted(deltas.items(), key=lambda kv: kv[1]):
        side = OrderSide.SELL if usd < 0 else OrderSide.BUY
        log.info("%s %s $%.2f", side.value, inv[cid], abs(usd))
        if dry_run:
            continue
        trading.submit_order(MarketOrderRequest(
            symbol=inv[cid], notional=abs(float(usd)), side=side,
            time_in_force=TimeInForce.GTC))


def run_once(p: Params, dry_run: bool = False):
    trading, data_client = make_clients()
    pairs = tradable_universe(trading)
    closes = fetch_daily_closes(data_client, pairs)
    # drop today's partial bar if present; signal on last completed day
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    closes = closes[closes.index < today]
    log.info("bars: %s rows, last close %s", len(closes), closes.index[-1].date())

    account = trading.get_account()
    equity = float(account.equity)

    state = load_state()
    breaker = Breaker(peak=state.get("peak", equity),
                      halt_days_left=state.get("halt_days_left", 0))
    if state.get("last_run") == str(closes.index[-1].date()):
        log.info("already ran for %s, nothing to do", state["last_run"])
        return
    scale = breaker.update(equity, p)
    log.info("equity $%.2f | drawdown %.1f%% | breaker scale %.1f",
             equity, breaker.drawdown * 100, scale)

    sig = compute_signals(closes, p)
    w_now = current_weights(trading, pairs, equity)
    rets = closes.pct_change()
    w_tgt = target_weights(sig, closes.index[-1], w_now, p,
                           breaker_scale=scale,
                           rets_window=rets.iloc[-p.vol_window:])
    log.info("targets: %s", {k: round(v, 3) for k, v in
                             w_tgt[w_tgt > 0].items()} or "all cash")
    rebalance(trading, pairs, w_now, w_tgt, equity, dry_run=dry_run)

    if not dry_run:
        save_state({"peak": breaker.peak,
                    "halt_days_left": breaker.halt_days_left,
                    "last_run": str(closes.index[-1].date()),
                    "equity": equity})
