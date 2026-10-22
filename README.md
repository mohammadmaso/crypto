# NobiMomentum – Nobitex auto-trading panel

BTC-gated, market-neutral momentum from `STRATEGY.md` (long top-5 / short bottom-5 by 14-day momentum, three
staggered 3-day sleeves), adapted to Nobitex: longs on spot, shorts as isolated **1x margin sell positions**.
Nobitex has no perpetuals, so the **margin position fee** replaces funding as the short-leg cost; it is modelled
and measured live. Capital is small, so the three sleeves are netted into one order per coin.

> Research candidate, not validated for real money. Follow the paper → testnet → live path below.

## Safety first
* The API key pair was pasted into a chat. **Rotate both keys before going live.** On Nobitex, give the new key
  only READ + TRADE (never WITHDRAW) and whitelist your IP.
* Keys live only in the git-ignored `.env`, never in the UI, DB or logs.
* **Not verified yet:** the build sandbox could not reach apidocs.nobitex.ir, so the request-signing string
  (`timestamp + METHOD + path?query + body`, in `backend/app/nobitex/client.py::sign`) has **not** been checked
  against Nobitex's official example vectors, and no call to Nobitex has been made. First thing to do on your Mac:
  Settings → **Test Nobitex keys** on testnet. If it returns an auth error, compare `sign()` with the docs.

## Setup (macOS)
```bash
brew install uv node
cp .env.example .env        # fill NOBITEX_API_KEY / NOBITEX_API_SECRET (testnet keys optional)
cd backend && uv sync && uv run pytest -q
cd ../frontend && npm install && npm run build
cd .. && ./scripts/start.sh                    # http://127.0.0.1:8000
```
Dev UI with hot reload: `cd frontend && npm run dev` (proxies `/api` to :8000).

## Run as a service
`./scripts/install_service.sh` installs a launchd agent (wrapped in `caffeinate -i`, restarts on crash). On
wake or restart, the scheduler's catch-up job runs the daily rebalance if the latest closed Tehran day has not run
and is still inside the catch-up window (default 12 h; older → skipped). Nobitex daily candles close at 00:00
Tehran (20:30 UTC); the job runs at 20:35 UTC (editable in Settings).

## Rollout
1. **Paper** (default): fills walk the live Nobitex order book with modelled fees. Run ≥ 6 months (STRATEGY §4).
2. **Testnet**: Settings → Testnet. Check Orders, Positions → reconciliation, and that **Kill** flattens.
3. **Live**: Settings → type the confirmation phrase → Go LIVE. Requires a successful `wallets/list` call. The bot
   starts paused; Resume when ready. Start with an amount you can lose entirely.

Every mode runs the same code; only the broker changes. Before each real run the book is reconciled with the
exchange; any mismatch blocks trading and alerts.

## Telegram (through your local proxy)
Settings → Telegram: bot token, chat id (send `/start` to the bot and it replies with your id), proxy
(`http://127.0.0.1:7890` or `socks5://…`), **Test proxy + token**. With **Require approval** on, each rebalance is
sent as a bundle with approve-all / reject / per-order toggles; no answer within the timeout → skipped and retried
next cycle. Commands: `/status /positions /report /leaderboard /pause /resume /kill /run`. Nobitex itself is
contacted directly (no proxy) unless you set one.

## Self-optimisation (shadow variants)
Live parameters never change on their own (STRATEGY §7). The 14 variants (lookback 7/14/30 × k 3/5 × gate on/off, plus the gated 7/14/30 rank-average combination for k 3/5)
run as forward paper books with Nobitex costs. A switch is suggested only after 90 days and a Sharpe/return margin,
and applied only when you approve it (Shadow page, or the button on the weekly Telegram suggestion). A weekly check also flags a short leg that is net-negative
after position fees.

## Risk layer
Fewer than 3 shortable coins → hold USDT (never a one-sided book). Gross ≤ 100 %, ≤ 25 % of a sleeve per coin, shorts 1x with collateral moved to the margin wallet first, liquidity
filter (order-book slippage ≤ 0.3 %), minimum order size, stale-data block, −25 % drawdown kill (flatten + pause),
reconciliation block. Gate off → close everything, hold USDT.

## Backtest
`Backtest` page: Binance research data (`~/kronos_iran/crypto/daily_full.pkl`, `funding_full.pkl`; override with
`NOBI_RESEARCH_DATA`) or stored Nobitex candles, with Nobitex or Binance cost models. The golden comparison against
`ls_study.pkl` (L14 k5, 1e-6) still needs those files; they are not in this repo, so it has not been run.

## Macro / regime overlay (backtest only, experimental)
Two overlays size the existing long-short book up or down; they never pick coins and the BTC gate still decides
whether to trade. Data (free, no keys): VIX, broad US dollar, US 10y yield (FRED) and the crypto Fear & Greed index,
plus BTC trend/volatility, market breadth and momentum dispersion computed from candles. Macro series are lagged a
day and z-scores are expanding, so nothing looks ahead.
* **Rule overlay (no ML):** each stress flag (VIX spike, dollar surge, yield surge, BTC volatility, sentiment
  collapse; z > 1.5) cuts exposure by 25 %, floor 25 %.
* **ML overlay:** walk-forward logistic regression predicting whether the base strategy's next 5-day return is
  positive; refit every 30 days on labels at least 5 days old; shows out-of-sample AUC (0.50 = no skill).
Backtest page → "Download macro data", then tick the two macro presets. The data downloads have **not** been tested
against the live endpoints (the build sandbox had no access). The PDF's own ML tests did not beat the simple rule, so
treat a good-looking result with suspicion until it also holds on data collected after today. Not wired into live
trading or the shadow books.

## Tests
`cd backend && uv run pytest -q` – signing round-trip, signal edge cases, tranche schedule, order planner, approval
timeout, catch-up window, paper-mode daily run (trade / gate off / stale data / kill), backtest.

## Layout
`backend/app` (config, nobitex client, data, strategy, exec, engine, scheduler, telegram, shadow, backtest, api) ·
`frontend/` (Vite + React + Tailwind) · `deploy/` + `scripts/` (launchd) · `backend/tests`.
