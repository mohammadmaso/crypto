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
                 capital: float = 1.0) -> BacktestResult:
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
            eq = sl.equity(prices)
            for fl in rebalance(sl, target_quantities(t.weights, eq, prices), prices, cost):
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


def summarize(res: BacktestResult) -> dict:
    r = res.returns
    eq = (1 + r).cumprod()
    return {
        "label": res.params.label,
        "stats": perf_stats(r),
        "post2021": perf_stats(r.loc["2022":]),
        "years": yearly(r),
        "contrib": {k: float(v) for k, v in res.breakdown.sum().items()},
        "equity": [{"day": str(d.date()), "equity": float(v)} for d, v in eq.iloc[:: max(1, len(eq) // 600)].items()],
    }


def np_safe(x):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else x
