# Mac setup — getting the bot running

A copy-paste walkthrough for macOS. Assumes you already have Python 3
installed (you do if you run other Python scripts). Goal: get to a
successful `--dry-run` (connects to Alpaca, shows what it *would* trade,
places no orders), then turn it on.

---

## Step 1 — Download the code (the right branch)

The bot lives on a branch called `claude/trading-strategy-150k-4oifjw`,
**not** `main`. You must select that branch before downloading, or the
`alpaca-bot` folder won't be in your download.

1. In your browser, go to:
   `https://github.com/ask21075-kalshi/kalshi-dashboard`
   (log in if it asks — the repo is private).
2. Near the top-left of the file list, click the **branch dropdown**
   (it probably says `main`).
3. Choose **`claude/trading-strategy-150k-4oifjw`**.
4. Click the green **`< > Code`** button (top-right of the file list).
5. Click **Download ZIP**.
6. In Finder, find the ZIP in your Downloads and **double-click to unzip**.
   You'll get a folder like
   `kalshi-dashboard-claude-trading-strategy-150k-4oifjw` containing an
   `alpaca-bot` folder. That `alpaca-bot` folder is all you need.

---

## Step 2 — Open Terminal in the bot folder

1. Open the **Terminal** app (Cmd+Space, type "Terminal", Enter).
2. Type `cd ` (with a space after it) but **don't press Enter yet**.
3. Drag the **`alpaca-bot`** folder from Finder directly onto the Terminal
   window — it auto-fills the full path. Now press **Enter**.
4. Confirm you're in the right place:
   ```
   ls
   ```
   You should see `run_live.py`, `requirements.txt`, `README.md`, etc.

---

## Step 3 — Install the bot's dependencies

```
pip3 install -r requirements.txt
```
This installs pandas, numpy, requests, and alpaca-py. It prints a lot of
text and ends without an error. (If `pip3` isn't found, try
`python3 -m pip install -r requirements.txt`.)

---

## Step 4 — Add your Alpaca keys

Create your private settings file from the template:
```
cp .env.example .env
```
Now open it in a simple editor right in the Terminal:
```
nano .env
```
You'll see the three lines. Use arrow keys to move; replace the
placeholders with your real **paper** keys so it looks like:
```
ALPACA_API_KEY=PKxxxxxxxxxxxxxxxxxxx
ALPACA_SECRET_KEY=your_long_secret_here
ALPACA_CAPITAL=10000
```
Save and exit nano: press **Ctrl+O**, then **Enter** (saves), then
**Ctrl+X** (exits).

> If you made a stray keys text file earlier somewhere else, ignore it —
> this `.env` inside `alpaca-bot` is the only one that matters.

---

## Step 5 — Test it (no real orders)

```
python3 run_live.py --dry-run
```
This connects to your Alpaca paper account, downloads prices, and prints
the trades it *would* make — **without placing any.** Success looks like a
few log lines ending in either some `BUY ...` lines or `targets: all cash`.

- `targets: all cash` is a perfectly valid result — it means the market
  regime filter says "stay out for now." Not a bug.
- If you see an auth error, double-check the keys in `.env` are your
  **Paper** keys (from the Paper section of app.alpaca.markets).

---

## Step 6 — Turn it on

When the dry run looks good, run it for real (paper money), looping daily:
```
python3 run_live.py --loop
```
It rebalances once per day and sleeps in between. **Leave this Terminal
window open** — closing it stops the bot. Since your Mac is always on,
that's fine for now.

To stop it: click the Terminal window and press **Ctrl+C**.

The bot remembers its state (cash, holdings, drawdown) in `state.json`, so
stopping and restarting is safe. To reset the $10k sleeve from scratch,
delete `state.json`.

---

## Later: making it permanent

`--loop` stops if the Terminal closes or the Mac restarts. When you're
ready, we can set it up as a background service (launchd) that survives
restarts and runs without a Terminal window open — or move it to a small
always-on cloud server. Not needed to start testing today.
