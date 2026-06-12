"""Pick the live config: best candidate on the trailing 3 years of data.

Writes best_params.json, which run_live.py loads. Re-run this quarterly
(or each January) — it is the same selection rule the walk-forward test
used, so live behavior matches what was tested.
"""
import json
from dataclasses import asdict

import pandas as pd

from bot import backtest
from bot.data import load_prices
from walkforward import CANDIDATES, score

PARAM_KEYS = ("mom_windows", "mom_weights", "ema_window", "regime_asset",
              "regime_ema", "top_k", "hold_k", "min_score", "vol_window",
              "target_vol", "band", "dd_soft", "dd_hard", "dd_cooldown",
              "min_history")


def main():
    px = load_prices(refresh=True)
    end = px.index[-1]
    start = end - pd.DateOffset(years=3)
    print(f"selection window: {start.date()} -> {end.date()}")
    results = {}
    for name, p in CANDIDATES.items():
        r = backtest.run(px, p, start=str(start.date()))
        results[name] = (score(r), r)
        print(f"  {name:13s} score={score(r):+.3f}  {r.summary().splitlines()[0]}")
    pick = max(results, key=lambda k: results[k][0])
    print(f"picked: {pick}")
    d = asdict(CANDIDATES[pick])
    with open("best_params.json", "w") as f:
        json.dump({k: d[k] for k in PARAM_KEYS}, f, indent=2, default=list)
    print("wrote best_params.json")


if __name__ == "__main__":
    main()
