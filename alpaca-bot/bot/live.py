"""Live (paper) trading runner for Alpaca.

Runs the same strategy code as the backtest, once per day, inside an
ISOLATED CAPITAL SLEEVE. The sleeve starts with a fixed budget (default
$10,000) and tracks its own cash and crypto holdings in state.json. It
never reads or modifies the rest of the account, so it can run safely
alongside other strategies on the same paper account — even if they trade
the same coins.

Each day:
  1. Pull ~1 year of daily crypto bars from Alpaca's data API.
  2. Value the sleeve (cash + held coins at the latest close).
  3. Compute target weights (momentum signals + breaker on SLEEVE equity).
  4. Rebalance toward those weights with notional market orders, sizing to
     the sleeve's own equity (sells first, then buys).
  5. Update the sleeve's cash/holdings from the intended fills.

Keys are read from the environment: ALPACA_API_KEY, ALPACA_SECRET_KEY.
The starting budget is ALPACA_CAPITAL (default 10000).
"""
import json
import logging
import os
import csv
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd

from .data import UNIVERSE
from .strategy import Breaker, Params, compute_signals, target_weights

log = logging.getLogger("bot")

STATE_PATH = os.path.join(os.path.dirname(__file__), "..", "state.json")
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "equity_log.csv")
LOG_FIELDS = ["date", "equity", "cash", "invested", "drawdown_pct",
              "breaker_scale", "positions"]
MIN_ORDER_USD = 10.0
FEE = 0.0025  # Alpaca crypto taker fee, applied to the sleeve's cash ledger


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


@dataclass
class Sleeve:
    """The bot's isolated book: its own cash and coin holdings.

    Deliberately independent of the Alpaca account's other positions so two
    strategies can share one paper account without interfering.
    """
    cash: float
    holdings: dict = field(default_factory=dict)  # asset id -> quantity
    peak: float = 0.0
    halt_days_left: int = 0
    last_run: str = ""

    @classmethod
    def load(cls, budget: float) -> "Sleeve":
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH) as f:
                d = json.load(f)
            return cls(cash=d["cash"], holdings=d.get("holdings", {}),
                       peak=d.get("peak", budget),
                       halt_days_left=d.get("halt_days_left", 0),
                       last_run=d.get("last_run", ""))
        log.info("no state file; starting fresh sleeve with $%.2f", budget)
        return cls(cash=budget, peak=budget)

    def save(self):
        with open(STATE_PATH, "w") as f:
            json.dump(asdict(self), f, indent=2)

    def holding_value(self, last_px: pd.Series) -> pd.Series:
        """Dollar value of each held coin at the latest close."""
        vals = {a: q * float(last_px.get(a, 0.0))
                for a, q in self.holdings.items()}
        return pd.Series(vals, dtype=float)

    def equity(self, last_px: pd.Series) -> float:
        return self.cash + float(self.holding_value(last_px).sum())

    def apply_fill(self, asset: str, usd: float, price: float):
        """Record an intended fill: usd>0 buys, usd<0 sells `asset`."""
        qty = usd / price
        self.holdings[asset] = self.holdings.get(asset, 0.0) + qty
        if abs(self.holdings[asset]) < 1e-12:
            self.holdings.pop(asset, None)
        self.cash -= usd + abs(usd) * FEE  # fee always costs cash


def positions_str(sleeve: Sleeve, last_px: pd.Series) -> str:
    """Compact human-readable holdings, e.g. 'btc:0.203 eth:0.150' or 'cash'."""
    val = sleeve.holding_value(last_px)
    eq = sleeve.equity(last_px)
    parts = [f"{a}:{v / eq:.3f}" for a, v in val.items() if v > 1e-9 and eq > 0]
    return " ".join(parts) if parts else "cash"


def append_log(date: str, sleeve: Sleeve, last_px: pd.Series,
               drawdown: float, scale, path: str = LOG_PATH) -> dict:
    """Append one daily row to the equity log, keeping a single row per date."""
    eq = sleeve.equity(last_px)
    row = {"date": date, "equity": f"{eq:.2f}", "cash": f"{sleeve.cash:.2f}",
           "invested": f"{eq - sleeve.cash:.2f}",
           "drawdown_pct": f"{drawdown * 100:.2f}",
           "breaker_scale": "" if scale is None else f"{scale:.2f}",
           "positions": positions_str(sleeve, last_px)}
    rows = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") != date]
    rows.append(row)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(rows)
    return row


def plan_orders(sleeve: Sleeve, pairs: dict, w_tgt: pd.Series,
                last_px: pd.Series) -> pd.Series:
    """Target-minus-current dollar trades per asset, dust filtered."""
    equity = sleeve.equity(last_px)
    held = sleeve.holding_value(last_px).reindex(list(pairs.values())).fillna(0.0)
    target = (w_tgt.reindex(held.index).fillna(0.0) * equity)
    deltas = (target - held).round(2)
    return deltas[deltas.abs() >= MIN_ORDER_USD]


def execute(trading, sleeve: Sleeve, pairs: dict, deltas: pd.Series,
            last_px: pd.Series, dry_run: bool = False):
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    inv = {cid: pair for pair, cid in pairs.items()}
    # sells first to free up cash for the buys
    for cid, usd in sorted(deltas.items(), key=lambda kv: kv[1]):
        side = OrderSide.SELL if usd < 0 else OrderSide.BUY
        log.info("%s %s $%.2f", side.value, inv[cid], abs(usd))
        if not dry_run:
            trading.submit_order(MarketOrderRequest(
                symbol=inv[cid], notional=abs(float(usd)), side=side,
                time_in_force=TimeInForce.GTC))
        sleeve.apply_fill(cid, float(usd), float(last_px[cid]))


def run_once(p: Params, budget: float = 10_000.0, dry_run: bool = False):
    trading, data_client = make_clients()
    pairs = tradable_universe(trading)
    closes = fetch_daily_closes(data_client, pairs)
    # drop today's partial bar if present; signal on last completed day
    today = pd.Timestamp.utcnow().tz_localize(None).normalize()
    closes = closes[closes.index < today]
    last_day = closes.index[-1]
    last_px = closes.iloc[-1]
    log.info("bars: %s rows, last close %s", len(closes), last_day.date())

    sleeve = Sleeve.load(budget)
    today_str = str(last_day.date())

    if sleeve.last_run == today_str:
        equity = sleeve.equity(last_px)
        dd = (1.0 - equity / sleeve.peak) if sleeve.peak > 0 else 0.0
        log.info("already ran for %s | sleeve equity $%.2f (cash $%.2f)",
                 sleeve.last_run, equity, sleeve.cash)
        if not dry_run:
            append_log(today_str, sleeve, last_px, dd, None)
        return

    equity = sleeve.equity(last_px)
    breaker = Breaker(peak=sleeve.peak, halt_days_left=sleeve.halt_days_left)
    scale = breaker.update(equity, p)
    log.info("sleeve equity $%.2f (cash $%.2f) | drawdown %.1f%% | scale %.1f",
             equity, sleeve.cash, breaker.drawdown * 100, scale)

    sig = compute_signals(closes, p)
    held_val = sleeve.holding_value(last_px).reindex(closes.columns).fillna(0.0)
    w_now = held_val / equity if equity > 0 else held_val * 0.0
    rets = closes.pct_change()
    w_tgt = target_weights(sig, last_day, w_now, p, breaker_scale=scale,
                           rets_window=rets.iloc[-p.vol_window:])
    log.info("targets: %s", {k: round(v, 3) for k, v in
                             w_tgt[w_tgt > 0].items()} or "all cash")

    deltas = plan_orders(sleeve, pairs, w_tgt, last_px)
    execute(trading, sleeve, pairs, deltas, last_px, dry_run=dry_run)

    if not dry_run:
        sleeve.peak = breaker.peak
        sleeve.halt_days_left = breaker.halt_days_left
        sleeve.last_run = today_str
        sleeve.save()
        row = append_log(today_str, sleeve, last_px, breaker.drawdown, scale)
        log.info("sleeve saved & logged: equity $%s, cash $%s, holding %s",
                 row["equity"], row["cash"], row["positions"])
