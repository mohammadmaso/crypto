"""Daily backtest of the tranche strategy. Uses the same sleeve accounting as the live books."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import StrategyParams
from ..strategy.book import CostModel, Sleeve, apply_short_carry, rebalance, target_quantities
from ..strategy.signal import Features, features, sleeve_rebalances_on, targets_for


@dataclass
class BacktestResult:
    params: StrategyParams
    returns: pd.Series
    equity: pd.Series
    breakdown: pd.DataFrame     # daily long_pnl, short_pnl, funding, position_fee, fees (fractions of prior equity)
    gate: pd.Series

    def stats(self, start=None, end=None) -> dict:
        return perf_stats(self.returns.loc[start:end])


def perf_stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 2:
        return {"days": len(r), "total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_dd": 0.0, "vol": 0.0}
    eq = (1 + r).cumprod()
    years = len(r) / 365.0
    sd = r.std()
    return {
        "days": int(len(r)),
        "total_return": float(eq.iloc[-1] - 1),
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if eq.iloc[-1] > 0 else -1.0,
        "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
        "max_dd": float((eq / eq.cummax() - 1).min()),
        "vol": float(sd * math.sqrt(365)),
    }


def run_backtest(close: pd.DataFrame, qvol: pd.DataFrame, shortable, p: StrategyParams, cost: CostModel,
                 funding: pd.DataFrame | None = None, start=None, end=None, feats: Features | None = None,
                 capital: float = 1.0, overlay: pd.Series | None = None) -> BacktestResult:
    """`overlay`: optional daily exposure multiplier in [0, 1] (macro/regime layer), applied to target weights on
    each sleeve's rebalance day. It must already be lagged so that it only uses information known at that close."""
    f = feats or features(close, qvol, p)
    days = f.close.loc[start:end].index
    px_all = f.close.ffill()
    sleeves = [Sleeve(cash=capital / p.tranches) for _ in range(p.tranches)]
    rets, eqs, rows = [], [], []
    prev_px: dict[str, float] | None = None
    prev_eq = capital
    for i, day in enumerate(days):
        row = px_all.loc[day]
        prices = row[row.notna()].to_dict()
        fund_row = funding.loc[day].dropna().to_dict() if funding is not None and day in funding.index else None
        lp = sp = fu = pf = fe = 0.0
        for sl in sleeves:
            if prev_px is not None:
                for s, q in sl.holdings.items():
                    d = q * (prices[s] - prev_px[s])
                    if q > 0:
                        lp += d
                    else:
                        sp += d
            a, b = apply_short_carry(sl, prices, cost, fund_row)
            fu += a
            pf += b
        for k, sl in enumerate(sleeves):
            if not sleeve_rebalances_on(i, k, p):
                continue
            t = targets_for(f, shortable, day, p)
            mult = float(overlay.get(day, 1.0)) if overlay is not None else 1.0
            weights = {s: w * mult for s, w in t.weights.items()}
            eq = sl.equity(prices)
            for fl in rebalance(sl, target_quantities(weights, eq, prices), prices, cost):
                fe += fl.fee
        eq_total = sum(sl.equity(prices) for sl in sleeves)
        rets.append(eq_total / prev_eq - 1 if prev_px is not None else 0.0)
        eqs.append(eq_total)
        base = prev_eq if prev_eq else 1.0
        rows.append((lp / base, sp / base, fu / base, -pf / base, -fe / base))
        prev_eq, prev_px = eq_total, prices
    idx = pd.Index(days)
    return BacktestResult(
        params=p,
        returns=pd.Series(rets, index=idx),
        equity=pd.Series(eqs, index=idx),
        breakdown=pd.DataFrame(rows, index=idx, columns=["long_pnl", "short_pnl", "funding", "position_fee", "fees"]),
        gate=f.gate.reindex(idx).fillna(False),
    )


def yearly(r: pd.Series) -> dict[str, float]:
    return {str(y): float((1 + g).prod() - 1) for y, g in r.groupby(r.index.year)}


def summarize(res: BacktestResult, label: str | None = None) -> dict:
    r = res.returns
    eq = (1 + r).cumprod()
    return {
        "label": label or res.params.label,
        "stats": perf_stats(r),
        "post2021": perf_stats(r.loc["2022":]),
        "years": yearly(r),
        "contrib": {k: float(v) for k, v in res.breakdown.sum().items()},
        "equity": [{"day": str(d.date()), "equity": float(v)} for d, v in eq.iloc[:: max(1, len(eq) // 600)].items()],
    }


def np_safe(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x


def _from_returns(r: pd.Series, p: StrategyParams, gate: pd.Series) -> BacktestResult:
    eq = (1 + r).cumprod()
    zeros = pd.DataFrame(0.0, index=r.index, columns=["long_pnl", "short_pnl", "funding", "position_fee", "fees"])
    zeros["long_pnl"] = r
    return BacktestResult(params=p, returns=r, equity=eq, breakdown=zeros, gate=gate)


def btc_baseline(close: pd.DataFrame, p: StrategyParams, cost: CostModel, trend: bool, start=None, end=None) -> BacktestResult:
    """BTC buy-and-hold, or BTC held only while it is above its SMA (PDF section 4-4). Costs on each switch."""
    btc = close["BTCUSDT"].ffill()
    gate = btc > btc.rolling(p.gate_sma, min_periods=p.gate_sma).mean()
    ret = btc.pct_change().fillna(0.0)
    if trend:
        pos = gate.shift(1, fill_value=False).astype(float)          # signal at close t, held over day t+1
        ret = ret * pos - pos.diff().abs().fillna(0.0) * cost.long_side
    return _from_returns(ret.loc[start:end], p, gate.loc[start:end])


def random_median(close, qvol, shortable, p: StrategyParams, cost: CostModel, funding=None, start=None, end=None,
                  seeds: int = 5) -> BacktestResult:
    """Same strategy with random picks; returns the run with the median final equity (null benchmark)."""
    f = features(close, qvol, p)
    runs = [run_backtest(close, qvol, shortable, p.model_copy(update={"selection": "random", "seed": s}), cost,
                         funding=funding, start=start, end=end, feats=f) for s in range(seeds)]
    runs.sort(key=lambda x: x.equity.iloc[-1])
    return runs[len(runs) // 2]
