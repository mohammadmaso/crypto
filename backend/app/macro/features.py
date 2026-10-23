"""Daily regime features. Every value at day t uses only information available at the day-t signal close.

Crypto features use day-t data (the strategy trades at that close). Macro series (US markets close after the Tehran
midnight candle) are lagged one day. Z-scores are *expanding*, so no future data leaks into any threshold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..strategy.signal import BTC, Features


def expanding_z(x: pd.Series, min_periods: int = 180) -> pd.Series:
    m = x.expanding(min_periods=min_periods).mean()
    s = x.expanding(min_periods=min_periods).std()
    return (x - m) / s.replace(0, np.nan)


def build(f: Features, macro: dict[str, pd.Series], universe: int = 30, lookback: int = 14) -> pd.DataFrame:
    idx = f.close.index
    out = pd.DataFrame(index=idx)
    btc = f.close[BTC].ffill()
    out["btc_trend"] = np.log(btc / btc.rolling(100, min_periods=100).mean())
    out["btc_vol"] = btc.pct_change().rolling(30, min_periods=20).std()
    mom = f.close / f.close.shift(lookback) - 1
    in_top = f.volrank.rank(axis=1, ascending=False, method="first") <= universe
    m = mom.where(in_top)
    out["breadth"] = (m > 0).sum(axis=1) / m.notna().sum(axis=1).replace(0, np.nan)
    out["dispersion"] = m.std(axis=1)
    out["btc_mom30"] = btc / btc.shift(30) - 1
    for name, s in macro.items():
        s = s.reindex(idx.union(s.index)).ffill().reindex(idx)
        if name == "fng":
            out["fng"] = s                                  # alternative.me publishes the day's value early
            out["fng_chg14"] = s - s.shift(14)
        else:
            s = s.shift(1)                                  # known only after the US close
            if name == "vix":
                out["vix"] = s
            else:
                out[f"{name}_chg30"] = s / s.shift(30) - 1 if name == "dxy" else s - s.shift(30)
    return out
