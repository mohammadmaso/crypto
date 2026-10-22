# Strategy spec: BTC-gated market-neutral crypto momentum (long top-k spot / short bottom-k perp)

Audience: an implementation agent. Status: **research candidate for paper trading. Not validated for real money.**
Everything below was measured by the code in `research/kronos_iran/` (commands at the end). Numbers are backtests on Binance data.

## 0. Verdict in five lines

1. After removing survivorship bias (including every delisted Binance USDT coin), **long-only momentum is no better than buying BTC**
   (best walk-forward long-only: +25.7%/yr vs BTC +20.2%/yr, drawdown -80% vs -77%).
2. A **market-neutral long-short version, switched on only while BTC is above its 100-day average**, kept a Sharpe near 1 after the 2020-21 boom
   (2022-2026: 31-33%/yr, Sharpe 0.97-1.12, beta to BTC ~0), beat 40 random long-short portfolios by a wide margin, and survived 2-4x higher costs.
3. It is **fragile in one respect**: only the 14-day lookback keeps the edge after 2021 (7-day: ~0%/yr; 30-day: 12-15%/yr); the edge decays; one setting was picked partly with hindsight.
4. Kronos (zero-shot and fine-tuned), 1-day momentum, ML rankers, regime rotation with gold/USD and Tehran-bourse momentum **did not help**; do not re-implement them (section 6).
5. Treat the backtest as an upper bound. Plan for roughly half the Sharpe live. Paper trade 6+ months before any real capital.

## 1. Strategy definition (the single approach to implement)

Parameters (do not tune; see section 7 for why):

| name | value |
|---|---|
| universe | Binance spot **USDT** pairs; at each UTC day the **30 most liquid** by trailing 30-day mean quote volume, requiring >= 30 daily candles of history |
| signal | `mom14 = close[t] / close[t-14] - 1` (daily UTC closes) |
| k | **5** long, **5** short (k=3 also tested: higher return, drawdown -47% vs -27%) |
| gate | trade only if `BTCUSDT close > its 100-day simple average` at the signal close; otherwise **close everything and hold USDT** |
| long leg | equal weight top-5 by `mom14`, **spot**, 50% of capital |
| short leg | equal weight bottom-5 by `mom14`, among coins that **have a USDT-M perpetual on that day**, via perpetual short, 50% of capital |
| shortable filter | a coin may be shorted only if its perp existed on that date (funding record exists); ~75% of eligible coin-days qualify |
| rebalance | every 3 days. **Three tranches**: capital split in 3 equal sleeves whose 3-day cycles start on consecutive days (removes rebalance-phase luck) |
| execution | signal at the daily close (00:00 UTC), orders filled at that close (backtest) |
| costs modelled | long leg 0.15% per side (0.10% taker + 0.05% slippage); short leg 0.10% per side (0.05% taker + 0.05% slippage); real funding applied to shorts daily |
| leverage | none (gross 100%, net ~0); no margin calls or liquidations were modelled |

Algorithm per sleeve, on each of its rebalance days `t` (UTC close):

```
eligible = top30_by_30d_quote_volume(t)  (point in time; includes coins that later delisted)
if not (BTC.close[t] > SMA100(BTC.close)[t]):  target = all cash
else:
    ranked = sort(eligible, by mom14 desc)
    longs  = ranked[:5]
    shorts = [c for c in reversed(ranked) if has_perp(c, t) and c not in longs][:5]
    target = +0.5*W/5 each long, -0.5*W/5 each short   (W = this sleeve's equity)
trade to target, pay fees, accrue funding on shorts daily (positive funding = shorts receive)
```
A delisted position is closed at its last available close. Cash earns 0%.

## 2. Evidence (backtest, survivorship-free universe of 684 USDT pairs, 199 of them ended/delisted)

All use 3 tranches, base costs, 2020-01 to 2026-10 unless stated. BTC buy and hold: 2020-2026 CAGR ~44%, drawdown -77%; 2022-2026 CAGR 13%, drawdown -67%. Note this strategy is market-neutral and is NOT designed to match BTC in bull years.

| variant (gated long-short) | CAGR | Sharpe | max drawdown | profitable 1-yr windows | corr. to BTC |
|---|---|---|---|---|---|
| **L14, k5 (this spec)** | **51%** | **1.33** | **-27%** | **84%** | 0.00 |
| L14, k3 | 49% | 1.11 | -49% | 77% | 0.01 |
| L7, k3 / k5 | 45% / 35% | 1.03 / 1.05 | -49% / -38% | 70% / 68% | 0 |
| L30, k3 / k5 | 41% / 36% | 0.98 / 1.06 | -46% / -37% | 73% / 78% | 0 |
| any lookback, **no BTC gate** | -9% to +17% | 0.12 to 0.58 | -66% to -91% | 37-67% | ~ -0.06 |

Decay and stability (CAGR / Sharpe):

| period | L14 k5 | L14 k3 | L30 k5 | L7 k5 |
|---|---|---|---|---|
| 2020-2021 | 112% / 1.76 | 98% / 1.41 | 103% / 1.73 | 199% / 2.44 |
| **2022-2026** | **31% / 1.12** | 33% / 0.97 | 15% / 0.65 | -2% / 0.03 |
| last 12 months (untouched) | +29% | +61% | +0% | -8% |

L14 k5 calendar years: 2020 +56%, 2021 +189%, 2022 -15%, 2023 +38%, 2024 +31%, 2025 +84%, 2026 YTD +29%. It is not meant to follow BTC (it lagged in 2020, 2023, 2024).

Robustness of L14 k3 (the variant stress-tested):
* random long-short portfolios with the same gate (40 draws): median total return -55%, 5-95% range -77% to +4%; the strategy +1,440% (100th percentile).
* short-leg cost 0.20%: 44%/yr (Sharpe 1.03); 0.40%: 34% (0.86); long 0.30% + short 0.40%: 27% (0.75, max DD -56%).
* negative funding rates tripled: 41%/yr (0.98); fill one full day late: 41%/yr (1.01); no costs and no funding: 56%/yr (1.20).
* worst single day -12% (k3) / -10% (k5); P&L split for k3 over the period: long leg +2.63, short leg +1.07, funding +0.31 (daily contributions summed).

What does NOT support the strategy (be honest about these):
* **Walk-forward re-selection of the setting each year** (choose the best of 12 variants on past data only) returned just **4.6%/yr (2022-26)** vs BTC 13.3%/yr and a 12-variant median of 7.0%: the best setting changes from year to year. The fixed 14-day choice is partly hindsight.
* The 2020-2021 boom produced most of the full-period return. Post-2021 numbers (31-33%/yr) are the realistic reference.
* 71% of (variant x year) cells were positive; median cell +34%, but 2022 was negative for every variant (-11% to -24%).

## 3. Data sources (public, no API keys)

* Daily spot candles: `GET https://data-api.binance.vision/api/v3/klines?symbol=XXXUSDT&interval=1d&startTime=..&limit=1000`
  (served for delisted symbols too). The full symbol list incl. delisted comes from the public archive listing
  `https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?delimiter=/&prefix=data/spot/monthly/klines/` (paginate with `marker`).
  Exclude stablecoins, fiat, wrapped coins and leveraged tokens (see `STABLE` / `LEVER` in `crypto_fetch.py`).
* Funding: `GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=XXXUSDT&startTime=..&limit=1000` (settlements 00:00/08:00/16:00 UTC, history since 2019-09).
  A funding record on a day = the perp existed that day.
* Live: use the same endpoints (candles at 00:00 UTC for the day just closed); order book depth endpoints for slippage measurement.
* Data caveat: the archive covers Binance only; coins listed elsewhere are absent; some delisted symbols have gaps; glitch candles (return > +400% or < -90%) were treated as missing in the backtest.

## 4. Implementation plan for the agent

Build as separate, testable components:

1. **Data ingestion**: daily candles + funding + perp-existence for the top-30 universe; store raw; validate (no gaps, UTC alignment); unit-test the universe ranking against `crypto_backtest.eligible`.
2. **Signal/targets**: pure function `(candles, funding_exists, date) -> target weights`, bit-for-bit the algorithm in section 1. Golden test: reproduce `ls_study.py` daily returns within 1e-6 using the saved candles.
3. **Execution layer**: paper broker first (fills at next-close or at a simulated book walk with the section-1 costs), then a real broker adapter behind the same interface. Reconcile positions against the exchange after every run; refuse to trade on a mismatch.
4. **Risk layer** (hard limits, outside the strategy): max gross 100%; max 25% of sleeve capital in one coin; max perp leverage 1x of that sleeve (isolated margin, keep collateral >= 100% of short notional so a +100% squeeze cannot liquidate); stop if any required data is stale > 1 day.
5. **Monitoring**: daily report (positions, P&L split long/short/funding/fees, slippage vs model, drawdown, gate status). Alert on: slippage > 2x model, funding paid > 0.1%/day on any short, drawdown thresholds below.

**Paper-trading protocol** (>= 6 months, ideally 12; same code path as live):
* Record the signal, the fill price vs the signal-close price, funding actually paid, and fees for every trade.
* Success criteria to proceed to small real capital: realised Sharpe > 0.5, max drawdown < 30%, measured round-trip cost on the long leg <= 0.4% and short leg <= 0.3%, and **no data/ops incident**.
* Kill criteria (stop and reassess): drawdown from peak > 25%; realised costs > 2x the model; three consecutive months negative while the gate was on; any exchange/withdrawal restriction.
* Start real capital at a small fixed amount that you can lose entirely; scale only after another 6 months match the paper results.

## 5. Risks and failure modes

* **Edge decay / regime dependence**: shorter lookbacks already died after 2021. Expect the 14-day edge to fade too; the gate itself is what protects (momentum crashes occur in downtrend rebounds).
* **Short squeezes and illiquid perps**: bottom-ranked coins can spike; no liquidation or order-book impact was modelled. Cap per-coin size; avoid coins with thin perps.
* **Funding is not a cost-free dial**: it was a small positive contributor in this sample, but can turn against shorts in euphoric markets.
* **Exchange and counterparty risk** (Binance outage, withdrawals frozen, delisting notices), regulatory access, tax treatment.
* **Backtest limits**: Binance-only universe; 25% of eligible coin-days not shortable and so not tested; delisted-coin histories can be incomplete; selection among lookbacks/k used the same data; cost assumptions (0.15%/0.10% per side) are estimates until measured live.

## 6. Tried and rejected (do not rebuild)

| idea | result |
|---|---|
| Kronos-base zero-shot / fine-tuned as a stock or coin picker (daily, weekly, hourly) | no tradable edge; daily top-5 on crypto -84% net, rank IC ~0; fine-tuned bourse model IC ~0.01-0.017 |
| 1-day momentum (long-only) | luck of rebalance phase; negative rank IC; -38% over 2018-26 (survivor universe) |
| ML ranker (gradient boosting, with funding/OI features) | high rank IC from a low-volatility tilt but portfolio ~ BTC; far below simple momentum after 2022 |
| Regime rotation stocks/gold/USD/rial | beat TEDPIX but lost to simply holding gold (a hindsight winner) |
| Tehran-bourse momentum | 12.9%/yr vs TEDPIX 48%/yr; 1-day version destroyed by 1.25% round-trip fees |
| Long-only improvements on the full universe | best: exclude 20% highest-funding coins (48%/yr, Sharpe 0.87, drawdown -88%); walk-forward still ~ BTC |
| Retention buffer, vol-target sizing, hedging long-only with a BTC short | buffer hurt (10-12%/yr); vol-target halves risk and return; short-BTC hedge 8%/yr |

## 7. Why the parameters are frozen

The headline setting was one of 12 variants (3 lookbacks x 2 sizes x gate on/off) and the 14-day choice was favoured by post-2021 data. Re-tuning on the same data would only
overfit further. The right way to improve is **forward evidence from paper trading**, not more backtest variants. If a change is wanted, specify it in advance and test it on data
collected after the paper-trading start date.

## 8. Reproduce the numbers

Environment: `~/kronos_iran/venv` (Python 3.12; pandas, numpy, scikit-learn, requests, torch for the Kronos parts). From `research/kronos_iran/`:

```
python crypto_fetch_full.py  DATA_DIR            # all USDT pairs incl. delisted -> daily_full.pkl
python crypto_funding_full.py DATA_DIR           # funding for every symbol that had a perp -> funding_full.pkl
python improve_study.py DATA_DIR OUT_DIR         # survivor vs full, long-only improvements, walk-forward, fee sensitivity
python ls_study.py     DATA_DIR OUT_DIR          # THIS strategy: grid, random null, stress, years, walk-forward, holdout
```
Key modules: `crypto_backtest.py` (universe `eligible`, panel), `crypto_ls.py` (long-short engine with funding, date-aware shortability, `allow_flat`),
`wengine.py` (long-only engine), `mom_study_crypto.py` (tranche, windows, stats). Raw outputs: `improve_results/improve_study.pkl`, `improve_results/ls_study.pkl`.
Reports: `Kronos_Crypto_Backtest.pdf` (Kronos on crypto), `Kronos_Iran_Backtest.pdf` (bourse), `Momentum_Persian_Guide.pdf` (momentum for non-experts; its absolute crypto numbers use a
survivor-only universe and are **too high**: use this file's numbers instead).
