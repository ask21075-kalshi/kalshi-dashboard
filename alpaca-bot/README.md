# Alpaca Crypto Momentum Bot

A daily momentum-rotation strategy for Alpaca **paper trading**, seeded with
$10,000. Long-only crypto (BTC, ETH, SOL, DOGE, AVAX, LINK, LTC, BCH, UNI,
AAVE, DOT, XRP), rebalanced once per day.

### Isolated capital sleeve

The bot manages its own **$10,000 sleeve**: it tracks its cash and coin
holdings in `state.json` and sizes every position against *that* budget, not
the account total. It never reads or touches the account's other positions,
so it runs safely alongside another strategy on the same paper account —
even one trading the same coins. Set the budget with `ALPACA_CAPITAL` (or
`--capital`). Drawdown and the circuit breaker track the sleeve's own
equity. To reset the sleeve, delete `state.json`.

## How it works

Each day, on daily closes:

1. **Momentum score** per asset: average of 7/30/90-day returns.
2. **Eligibility**: price above its 50-day EMA, positive momentum.
3. **Regime filter**: everything goes to cash when BTC is below its 100-day
   EMA (this single rule turned 2022 from −70% into −10% in testing).
4. **Portfolio**: top 3 eligible assets, inverse-volatility weighted, gross
   exposure scaled to ~60% annualized vol, never leveraged.
5. **Cost control**: rank hysteresis and a 5% rebalance band keep turnover
   (and Alpaca's ~0.25% taker fee) down.
6. **Circuit breaker**: −20% drawdown halves exposure; −30% goes flat for
   10 days, then resets and resumes.

The exact config in `best_params.json` is re-picked from a small candidate
menu (`walkforward.py CANDIDATES`) by trailing-3-year performance — re-run
`select_params.py` quarterly to keep it current.

## Honest performance numbers

Backtested on Coin Metrics daily data with 0.35%/side costs (Alpaca taker
fee + slippage). Signals trade with no look-ahead.

**Walk-forward test** (each year's config chosen using only prior years),
2018-01 → 2026-05, $10k start:

| | |
|---|---|
| CAGR | **+30.5%/yr** |
| Max drawdown | 54% |
| Sharpe | 0.77 |
| Final equity | **$93,730** |

Per year: 2018 −32%, 2019 +3%, 2020 +299%, 2021 +376%, 2022 −11%,
2023 +14%, 2024 +6%, 2025 −24%, 2026 −14% (partial).

Read that table carefully before believing anything:

- **Returns are lumpy.** Nearly all profit came from 2020–21. The strategy's
  job in other years is mostly *not losing much* while waiting for the next
  trend. The trailing 3 years were roughly flat-to-down.
- **The in-sample/out-of-sample gap is real.** Naively tuned parameters
  showed +64%/yr in-sample but only +7%/yr out-of-sample. The walk-forward
  number above is the defensible one.
- **About the $150k/yr goal:** $150k/yr from $10k is a 1,500% annual return;
  no honest strategy clears that. At the walk-forward rate (~30%/yr), $10k
  compounds to ~$100k in 8 years with everything reinvested. Generating
  $150k *per year* at ~30%/yr requires roughly **$500k of capital**. The
  realistic path is: paper trade this, then compound a growing seed — not
  income from day one.
- Past performance, simulated performance especially, does not predict
  future results. Crypto can gap through stops; the breaker limits but does
  not cap losses.

## Setup

```bash
cd alpaca-bot
pip install -r requirements.txt

# 1. keys: create a Paper account at https://app.alpaca.markets
cp .env.example .env   # then paste your paper API key + secret into .env

# 2. pick the current config (re-run quarterly)
python select_params.py

# 3. sanity-check what it would do, no orders submitted
python run_live.py --dry-run

# 4. trade daily on the paper account
python run_live.py --loop          # long-running process, or:
python run_live.py                 # single run, schedule via cron:
# 15 0 * * *  cd /path/to/alpaca-bot && python run_live.py >> bot.log 2>&1
```

`state.json` holds the circuit-breaker state between runs; delete it to
reset. The bot only ever uses `paper=True` — point it at real money only by
editing the code deliberately, and don't do that until it has months of
good paper history.

## Repo map

| file | purpose |
|---|---|
| `bot/strategy.py` | signal + sizing logic (shared by backtest and live) |
| `bot/backtest.py` | daily backtest engine with fees |
| `bot/live.py` | Alpaca paper-trading runner |
| `bot/data.py` | Coin Metrics historical data loader |
| `run_backtest.py` | backtest CLI |
| `run_live.py` | live CLI (`--dry-run`, `--loop`) |
| `select_params.py` | picks `best_params.json` from trailing 3y |
| `walkforward.py` | the honest walk-forward evaluation |
| `sweep.py` | in-sample parameter exploration |
| `tests/test_smoke.py` | offline tests (`python tests/test_smoke.py`) |
