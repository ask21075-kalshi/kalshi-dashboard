"""Run one daily rebalance against Alpaca paper trading.

Usage:
    python run_live.py [--dry-run] [--loop]

--dry-run  compute and log orders without submitting them
--loop     keep running, rebalancing once per UTC day at 00:10
Reads ALPACA_API_KEY / ALPACA_SECRET_KEY from the environment or a .env
file in this directory.
"""
import argparse
import logging
import os
import sys
import time

from bot.live import run_once
from bot.params import load_params

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bot")


def load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--capital", type=float, default=None,
                    help="starting sleeve budget (overrides ALPACA_CAPITAL)")
    args = ap.parse_args()

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    if "ALPACA_API_KEY" not in os.environ:
        sys.exit("Set ALPACA_API_KEY and ALPACA_SECRET_KEY (see .env.example)")

    budget = (args.capital if args.capital is not None
              else float(os.environ.get("ALPACA_CAPITAL", "10000")))
    p = load_params()
    log.info("params: %s", p)
    log.info("capital sleeve: $%.2f (isolated from the rest of the account)",
             budget)
    while True:
        try:
            run_once(p, budget=budget, dry_run=args.dry_run)
        except Exception:
            log.exception("run failed")
            if not args.loop:
                raise
        if not args.loop:
            break
        # sleep until next 00:10 UTC
        now = time.time()
        next_run = (now // 86400 + 1) * 86400 + 600
        log.info("sleeping %.1f h", (next_run - now) / 3600)
        time.sleep(next_run - now)


if __name__ == "__main__":
    main()
