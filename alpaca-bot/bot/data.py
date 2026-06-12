"""Historical daily price data from Coin Metrics community data (GitHub).

Used for backtesting. The live bot pulls bars from Alpaca's data API instead.
"""
import os

import pandas as pd
import requests

COINMETRICS_URL = "https://raw.githubusercontent.com/coinmetrics/data/master/csv/{sym}.csv"

# Coin Metrics asset id -> Alpaca trading pair
UNIVERSE = {
    "btc": "BTC/USD",
    "eth": "ETH/USD",
    "sol": "SOL/USD",
    "doge": "DOGE/USD",
    "avax": "AVAX/USD",
    "link": "LINK/USD",
    "ltc": "LTC/USD",
    "bch": "BCH/USD",
    "uni": "UNI/USD",
    "aave": "AAVE/USD",
    "dot": "DOT/USD",
    "xrp": "XRP/USD",
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def _load_one(sym: str, refresh: bool = False) -> pd.Series:
    path = os.path.join(DATA_DIR, f"{sym}.csv")
    if refresh or not os.path.exists(path):
        os.makedirs(DATA_DIR, exist_ok=True)
        resp = requests.get(COINMETRICS_URL.format(sym=sym), timeout=60)
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)
    df = pd.read_csv(path, low_memory=False)
    price_col = "PriceUSD" if "PriceUSD" in df.columns else "ReferenceRateUSD"
    s = df.set_index(pd.to_datetime(df["time"]))[price_col].dropna()
    s.name = sym
    return s


def load_prices(refresh: bool = False) -> pd.DataFrame:
    """Daily close prices, one column per asset, NaN before an asset existed."""
    return pd.concat([_load_one(s, refresh) for s in UNIVERSE], axis=1).sort_index()
