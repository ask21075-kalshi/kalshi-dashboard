"""Coordinate-descent parameter sweep.

Tunes on the in-sample window only (2018-2022); 2023+ is reserved for
out-of-sample validation and never touched here.
"""
import itertools
import json
from dataclasses import replace
from multiprocessing import Pool

from bot import backtest
from bot.data import load_prices
from bot.strategy import Params

IS_START, IS_END = "2018-01-01", "2022-12-31"

GRID = {
    "mom_windows": [(7, 30, 90), (14, 30, 90), (21, 63, 126), (30, 90, 180),
                    (30, 90), (90,)],
    "mom_weights": None,  # derived: equal weights for the chosen windows
    "ema_window": [20, 30, 50, 100],
    "regime_ema": [50, 100, 150, 200],
    "top_k": [1, 2, 3, 4],
    "target_vol": [0.40, 0.60, 0.80, 1.00],
    "band": [0.03, 0.05, 0.10],
    "dd_soft": [0.15, 0.20, 0.25],
    "vol_window": [10, 20, 30],
}

px = load_prices()


def fix(p: Params) -> Params:
    n = len(p.mom_windows)
    return replace(p, mom_weights=tuple([1.0 / n] * n),
                   hold_k=p.top_k + 2, dd_hard=p.dd_soft + 0.10)


def score(p: Params):
    r = backtest.run(px, p, start=IS_START, end=IS_END)
    return r.cagr / (r.max_dd + 0.10), r


def eval_one(args):
    key, val = args
    p = fix(replace(BASE, **{key: val}))
    s, r = score(p)
    return key, val, s, r.summary().split("\n")[0]


if __name__ == "__main__":
    BASE = fix(Params())
    best_s, best_r = score(BASE)
    print(f"baseline score={best_s:.3f}")
    for sweep_pass in range(3):
        improved = False
        for key, vals in GRID.items():
            if vals is None:
                continue
            jobs = [(key, v) for v in vals if v != getattr(BASE, key)]
            with Pool(4) as pool:
                results = pool.map(eval_one, jobs)
            for k, v, s, line in sorted(results, key=lambda x: -x[2]):
                print(f"  {k}={v}: score={s:.3f}  {line}")
            k, v, s, _ = max(results, key=lambda x: x[2])
            if s > best_s + 1e-6:
                BASE = fix(replace(BASE, **{k: v}))
                best_s = s
                improved = True
                print(f"PASS {sweep_pass}: take {k}={v} (score {s:.3f})")
        if not improved:
            break
    print("\nBEST PARAMS:", BASE)
    print("IS result:", score(BASE)[1].summary())
    with open("best_params.json", "w") as f:
        json.dump({k: getattr(BASE, k) for k in (
            "mom_windows", "mom_weights", "ema_window", "regime_ema",
            "regime_asset", "top_k", "hold_k", "vol_window", "target_vol",
            "band", "dd_soft", "dd_hard", "dd_cooldown", "min_history")},
            f, indent=2, default=list)
