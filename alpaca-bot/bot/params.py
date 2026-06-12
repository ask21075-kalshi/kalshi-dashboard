"""Load tuned parameters from best_params.json, falling back to defaults."""
import json
import os

from .strategy import Params

PARAMS_PATH = os.path.join(os.path.dirname(__file__), "..", "best_params.json")


def load_params() -> Params:
    if os.path.exists(PARAMS_PATH):
        with open(PARAMS_PATH) as f:
            d = json.load(f)
        d["mom_windows"] = tuple(d["mom_windows"])
        d["mom_weights"] = tuple(d["mom_weights"])
        return Params(**d)
    return Params()
