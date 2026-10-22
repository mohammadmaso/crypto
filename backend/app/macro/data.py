"""Free macro and sentiment series used by the regime overlay (all daily, cached on disk).

  vix  : CBOE VIX            (FRED VIXCLS)       equity-market fear
  dxy  : broad US dollar     (FRED DTWEXBGS)     global liquidity / risk appetite
  y10  : US 10y yield        (FRED DGS10)        rates
  fng  : Crypto Fear & Greed (alternative.me)    crypto sentiment (since 2018)

These are fetched through the research-data proxy from Settings (not through Nobitex). Nothing here needs an API key.
"""
from __future__ import annotations

import io
import logging
import time

import httpx
import pandas as pd

from ..config import DATA_DIR

log = logging.getLogger(__name__)

DIR = DATA_DIR / "macro"
FRED = {"vix": "VIXCLS", "dxy": "DTWEXBGS", "y10": "DGS10"}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={id}"
FNG_URL = "https://api.alternative.me/fng/?limit=0&format=json"


def parse_fred(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text))
    s = pd.to_numeric(df.iloc[:, 1], errors="coerce")          # "." marks holidays
    s.index = pd.to_datetime(df.iloc[:, 0]).dt.normalize()
    return s.dropna().sort_index()


def parse_fng(payload: dict) -> pd.Series:
    rows = payload.get("data") or []
    s = pd.Series({pd.Timestamp(int(r["timestamp"]), unit="s").normalize(): float(r["value"]) for r in rows})
    return s.sort_index()


def sync(proxy: str | None = None) -> dict:
    """Download every series; returns {name: rows or error}. Existing cache is kept for series that fail."""
    DIR.mkdir(parents=True, exist_ok=True)
    out: dict = {}
    with httpx.Client(proxy=proxy or None, timeout=30, trust_env=False, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 NobiMomentum"}) as c:
        for name, sid in FRED.items():
            try:
                r = c.get(FRED_URL.format(id=sid))
                r.raise_for_status()
                s = parse_fred(r.text)
                s.rename(name).to_csv(DIR / f"{name}.csv", header=True)
                out[name] = len(s)
            except Exception as e:  # noqa: BLE001
                out[name] = f"error: {e}"
        try:
            r = c.get(FNG_URL)
            r.raise_for_status()
            s = parse_fng(r.json())
            s.rename("fng").to_csv(DIR / "fng.csv", header=True)
            out["fng"] = len(s)
        except Exception as e:  # noqa: BLE001
            out["fng"] = f"error: {e}"
    return out


def load() -> dict[str, pd.Series]:
    res = {}
    for name in [*FRED, "fng"]:
        f = DIR / f"{name}.csv"
        if f.exists():
            df = pd.read_csv(f, index_col=0, parse_dates=True)
            res[name] = df.iloc[:, 0].astype(float)
    return res


def status() -> dict:
    series = load()
    return {name: {"rows": len(s), "first": str(s.index.min().date()), "last": str(s.index.max().date())}
            for name, s in series.items()} | {"missing": [n for n in [*FRED, "fng"] if n not in series],
                                              "checked": time.time()}
