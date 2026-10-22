"""Loader for the survivorship-free Binance research data in ~/kronos_iran/crypto (see STRATEGY.md section 3)."""
from __future__ import annotations

import pickle
from functools import lru_cache
from pathlib import Path

import pandas as pd

from ..config import RESEARCH_DATA


@lru_cache(maxsize=1)
def load(data_dir: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (close, quote_volume, funding_daily). Shortable on a day = funding record exists that day."""
    d = Path(data_dir) if data_dir else RESEARCH_DATA
    with open(d / "daily_full.pkl", "rb") as fh:
        daily = pickle.load(fh)
    with open(d / "funding_full.pkl", "rb") as fh:
        funding = pickle.load(fh)
    close = daily["c"].astype(float)
    close.index = pd.DatetimeIndex(close.index).normalize()
    qv = daily["q"].astype(float).reindex_like(close)
    qv.index = close.index
    funding = funding.astype(float)
    funding.index = pd.DatetimeIndex(funding.index).normalize()
    funding = funding.reindex(index=close.index, columns=close.columns)
    return close, qv, funding


def available() -> bool:
    return (RESEARCH_DATA / "daily_full.pkl").exists() and (RESEARCH_DATA / "funding_full.pkl").exists()
