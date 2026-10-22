"""STRATEGY.md section 1: BTC-gated market-neutral momentum. Pure functions, no I/O.

Inputs are wide DataFrames indexed by day with one column per symbol (e.g. BTCUSDT):
  close  : daily closes, glitch candles already set to NaN
  qvol   : daily quote volume (NaN when there was no candle)
  shortable : bool DataFrame (same shape) or a set of symbols shortable on every day
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import StrategyParams

BTC = "BTCUSDT"


@dataclass
class Features:
    close: pd.DataFrame
    mom: pd.DataFrame
    volrank: pd.DataFrame      # trailing mean quote volume (NaN where ineligible by history)
    gate: pd.Series            # BTC close > SMA(n)


def clean_glitches(close: pd.DataFrame, hi: float = 4.0, lo: float = -0.9) -> pd.DataFrame:
    """Treat candles with a daily return > +400% or < -90% as missing (as in the backtest)."""
    r = close / close.ffill().shift(1) - 1
    return close.mask((r > hi) | (r < lo))


def features(close: pd.DataFrame, qvol: pd.DataFrame, p: StrategyParams) -> Features:
    close = close.sort_index()
    qvol = qvol.reindex_like(close)
    mom = close / close.shift(p.lookback) - 1
    history = close.notna().cumsum()
    vol = qvol.rolling(p.volume_window, min_periods=1).mean()
    vol = vol.where((history >= p.min_history) & close.notna())
    if BTC in close:
        btc = close[BTC].ffill()
        gate = btc > btc.rolling(p.gate_sma, min_periods=p.gate_sma).mean()
    else:
        gate = pd.Series(False, index=close.index)
    return Features(close=close, mom=mom, volrank=vol, gate=gate)


@dataclass
class Targets:
    day: pd.Timestamp
    gate: bool
    weights: dict[str, float] = field(default_factory=dict)   # fraction of sleeve equity, signed
    eligible: list[str] = field(default_factory=list)
    ranked: list[tuple[str, float]] = field(default_factory=list)
    longs: list[str] = field(default_factory=list)
    shorts: list[str] = field(default_factory=list)


def eligible_universe(f: Features, day, size: int) -> list[str]:
    v = f.volrank.loc[day].dropna()
    return list(v.sort_values(ascending=False, kind="mergesort").index[:size])


def is_shortable(shortable, sym: str, day) -> bool:
    if isinstance(shortable, pd.DataFrame):
        return bool(sym in shortable.columns and day in shortable.index and shortable.at[day, sym])
    return sym in shortable


def targets_for(f: Features, shortable, day, p: StrategyParams) -> Targets:
    gate = bool(f.gate.get(day, False)) if p.gate_on else True
    out = Targets(day=day, gate=gate)
    eligible = eligible_universe(f, day, p.universe_size)
    out.eligible = eligible
    mom = f.mom.loc[day, eligible].dropna() if eligible else pd.Series(dtype=float)
    ranked = mom.sort_values(ascending=False, kind="mergesort")
    out.ranked = [(s, float(m)) for s, m in ranked.items()]
    if not gate or ranked.empty:
        return out
    longs = list(ranked.index[: p.k])
    shorts = [s for s in reversed(ranked.index) if s not in longs and is_shortable(shortable, s, day)][: p.k]
    out.longs, out.shorts = longs, shorts
    for s in longs:
        out.weights[s] = p.long_frac / len(longs)
    for s in shorts:
        out.weights[s] = -p.short_frac / len(shorts)
    return out


def sleeve_rebalances_on(day_index: int, sleeve: int, p: StrategyParams) -> bool:
    """Sleeve i starts its 3-day cycle i days after the first day."""
    return day_index >= sleeve and (day_index - sleeve) % p.rebalance_days == 0


def all_variants() -> list[StrategyParams]:
    """The 12 variants from STRATEGY.md section 7 (3 lookbacks x 2 sizes x gate on/off)."""
    return [StrategyParams(lookback=lb, k=k, gate_on=g) for lb in (7, 14, 30) for k in (3, 5) for g in (True, False)]


def nan_to_none(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)
