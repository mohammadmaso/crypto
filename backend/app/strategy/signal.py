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
    vol: pd.DataFrame | None = None   # 30d std of daily returns (only when weighting="invvol")
    moms: dict[int, pd.DataFrame] = field(default_factory=dict)   # one momentum frame per combo lookback


COMBO_LOOKBACKS = (7, 14, 30)


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
    moms = {L: close / close.shift(L) - 1 for L in COMBO_LOOKBACKS} if p.combo else {}
    risk = close.pct_change().rolling(30, min_periods=20).std() if p.weighting == "invvol" else None
    return Features(close=close, mom=mom, volrank=vol, gate=gate, moms=moms, vol=risk)


def combo_score(f: Features, day, eligible: list[str]) -> pd.Series:
    """Mean of the cross-sectional percentile ranks (within the eligible universe) of each lookback's momentum.
    A coin needs all lookbacks available. Higher = stronger."""
    cols = pd.DataFrame({L: m.loc[day, eligible] for L, m in f.moms.items()}).dropna()
    return cols.rank(pct=True).mean(axis=1)


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
    if not eligible:
        mom = pd.Series(dtype=float)
    elif p.combo:
        mom = combo_score(f, day, eligible)
    else:
        mom = f.mom.loc[day, eligible].dropna()
    ranked = mom.sort_values(ascending=False, kind="mergesort")
    if p.selection == "random" and not ranked.empty:      # null benchmark: same universe, random order
        rng = np.random.default_rng([p.seed, day.toordinal()])
        ranked = ranked.iloc[rng.permutation(len(ranked))]
    out.ranked = [(s, float(m)) for s, m in ranked.items()]
    if not gate or ranked.empty:
        return out
    longs = list(ranked.index[: p.k])
    shorts = [s for s in reversed(ranked.index) if s not in longs and is_shortable(shortable, s, day)][: p.k] \
        if p.short_frac > 0 else []
    if p.short_frac > 0 and len(shorts) < min(p.min_shorts, p.k):
        return out          # cannot stay market-neutral: hold cash rather than run a one-sided book
    out.longs, out.shorts = longs, shorts
    for names, frac, sign in ((longs, p.long_frac, 1), (shorts, p.short_frac, -1)):
        for s, w in _leg_weights(f, day, names, frac * p.exposure, p).items():
            out.weights[s] = sign * w
    return out


def _leg_weights(f: Features, day, names: list[str], frac: float, p: StrategyParams) -> dict[str, float]:
    if not names or frac <= 0:
        return {}
    if p.weighting == "invvol" and f.vol is not None:
        v = f.vol.loc[day, names]
        if v.notna().all() and (v > 0).all():
            inv = 1.0 / v
            return {s: float(frac * inv[s] / inv.sum()) for s in names}
    return {s: frac / len(names) for s in names}


def sleeve_rebalances_on(day_index: int, sleeve: int, p: StrategyParams) -> bool:
    """Sleeve i starts its 3-day cycle i days after the first day."""
    return day_index >= sleeve and (day_index - sleeve) % p.rebalance_days == 0


def all_variants() -> list[StrategyParams]:
    """The 12 variants from STRATEGY.md section 7 (3 lookbacks x 2 sizes x gate on/off) plus the gated
    7/14/30 combination (PDF section 7) with k=3 and k=5."""
    base = [StrategyParams(lookback=lb, k=k, gate_on=g) for lb in (7, 14, 30) for k in (3, 5) for g in (True, False)]
    return base + [StrategyParams(combo=True, k=k) for k in (3, 5)]


def nan_to_none(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else float(x)
