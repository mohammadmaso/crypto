"""Nobitex daily candle ingest.

Nobitex daily candles open at 00:00 Asia/Tehran (20:30 UTC). A candle is stored under its Tehran day and only
once it has closed. The UDF endpoint returns at most ~500 bars per call, so history is paged.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlmodel import select

from ..db import Candle, session
from ..nobitex.client import NobitexClient, NobitexError

log = logging.getLogger(__name__)

TEHRAN = timedelta(hours=3, minutes=30)
DAY = 86400
PAGE_BARS = 450

# Not tradable momentum assets: stablecoins, fiat-pegged and tokenised gold.
EXCLUDE = {"usdt", "usdc", "dai", "tusd", "busd", "fdusd", "usde", "usd1", "pyusd", "eurc", "xaut", "paxg", "rls"}


def tehran_day(ts: int) -> date:
    return (datetime.fromtimestamp(ts, timezone.utc) + TEHRAN).date()


def last_closed_day(now: float | None = None) -> date:
    """Most recent Tehran day whose daily candle has closed."""
    now = time.time() if now is None else now
    return (datetime.fromtimestamp(now, timezone.utc) + TEHRAN).date() - timedelta(days=1)


def day_open_ts(d: date) -> int:
    return int((datetime(d.year, d.month, d.day, tzinfo=timezone.utc) - TEHRAN).timestamp())


def symbol_of(src: str, dst: str = "usdt") -> str:
    return f"{src.upper()}{dst.upper()}"


async def usdt_markets(client: NobitexClient) -> dict[str, dict]:
    """symbol -> stats for active USDT spot markets (excluding stablecoins/gold)."""
    stats = await client.stats(dst="usdt")
    out = {}
    for k, v in stats.items():
        src, _, dst = k.partition("-")
        if dst != "usdt" or src in EXCLUDE or v.get("isClosed"):
            continue
        out[symbol_of(src)] = {"src": src, **v}
    return out


async def fetch_history(client: NobitexClient, symbol: str, start: date, end: date) -> list[Candle]:
    rows: list[Candle] = []
    frm = day_open_ts(start)
    to_final = day_open_ts(end) + DAY - 1
    while frm <= to_final:
        to = min(frm + PAGE_BARS * DAY, to_final)
        try:
            d = await client.udf_history(symbol, "D", frm, to)
        except NobitexError as e:
            log.warning("udf %s failed: %s", symbol, e)
            break
        if d.get("s") == "ok":
            for t, o, h, l, c, v in zip(d["t"], d["o"], d["h"], d["l"], d["c"], d["v"]):
                day = tehran_day(int(t))
                if start <= day <= end and c:
                    rows.append(Candle(symbol=symbol, day=day, o=o, h=h, l=l, c=c, v=v or 0.0, q=(v or 0.0) * c))
        frm = to + 1
    return rows


def stored_last_day(symbol: str) -> date | None:
    with session() as s:
        r = s.exec(select(Candle.day).where(Candle.symbol == symbol).order_by(Candle.day.desc()).limit(1)).first()
        return r


def upsert(rows: list[Candle]) -> int:
    if not rows:
        return 0
    with session() as s:
        existing = {(c.symbol, c.day): c for c in s.exec(
            select(Candle).where(Candle.symbol == rows[0].symbol, Candle.day >= min(r.day for r in rows)))}
        for r in rows:
            e = existing.get((r.symbol, r.day))
            if e:
                e.o, e.h, e.l, e.c, e.v, e.q = r.o, r.h, r.l, r.c, r.v, r.q
                s.add(e)
            else:
                s.add(r)
        s.commit()
    return len(rows)


async def sync(client: NobitexClient, symbols: list[str], history_days: int = 400, concurrency: int = 4) -> dict:
    """Bring every symbol up to the last closed day. Re-fetches the last 2 stored days to correct late prints."""
    end = last_closed_day()
    sem = asyncio.Semaphore(concurrency)
    counts: dict[str, int] = {}

    async def one(sym: str) -> None:
        async with sem:
            last = stored_last_day(sym)
            start = (last - timedelta(days=2)) if last else end - timedelta(days=history_days)
            if last and last >= end:
                counts[sym] = 0
                return
            counts[sym] = upsert(await fetch_history(client, sym, start, end))
            await asyncio.sleep(0.2)

    await asyncio.gather(*(one(s) for s in symbols))
    return {"end": str(end), "updated": sum(counts.values()), "symbols": len(symbols)}


def panel(symbols: list[str] | None = None, since: date | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wide (close, quote_volume) frames indexed by Tehran day."""
    with session() as s:
        q = select(Candle.symbol, Candle.day, Candle.c, Candle.q)
        if since:
            q = q.where(Candle.day >= since)
        rows = s.exec(q).all()
    if not rows:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.DataFrame(rows, columns=["symbol", "day", "c", "q"])
    if symbols is not None:
        df = df[df.symbol.isin(symbols)]
    df["day"] = pd.to_datetime(df["day"])
    close = df.pivot(index="day", columns="symbol", values="c").sort_index()
    full = pd.date_range(close.index.min(), close.index.max(), freq="D")
    close = close.reindex(full)
    qv = df.pivot(index="day", columns="symbol", values="q").reindex(full)
    return close, qv


def data_age_hours(close: pd.DataFrame, now: float | None = None) -> float:
    """Hours since the newest stored candle closed."""
    if close.empty:
        return float("inf")
    last = close.index.max().date() + timedelta(days=1)
    closed_at = day_open_ts(last)
    return ((time.time() if now is None else now) - closed_at) / 3600
