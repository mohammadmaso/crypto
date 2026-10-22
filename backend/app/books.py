"""Persistence of sleeve books (live book and shadow variant books)."""
from __future__ import annotations

from datetime import date

from sqlmodel import select

from .db import BookState, EquityPoint, session
from .strategy.book import Sleeve

LIVE = "live"


def load_book(book: str, tranches: int, capital: float) -> list[Sleeve]:
    with session() as s:
        rows = list(s.exec(select(BookState).where(BookState.book == book).order_by(BookState.sleeve)))
    if len(rows) != tranches:
        return [Sleeve(cash=capital / tranches) for _ in range(tranches)]
    return [Sleeve(cash=r.cash, holdings=dict(r.holdings or {}), entry=dict(r.entry or {})) for r in rows]


def book_exists(book: str) -> bool:
    with session() as s:
        return s.exec(select(BookState.id).where(BookState.book == book).limit(1)).first() is not None


def save_book(book: str, sleeves: list[Sleeve], last_rebalance: dict[int, date] | None = None, params: dict | None = None) -> None:
    with session() as s:
        rows = {r.sleeve: r for r in s.exec(select(BookState).where(BookState.book == book))}
        for k in list(rows):
            if k >= len(sleeves):
                s.delete(rows.pop(k))
        for i, sl in enumerate(sleeves):
            r = rows.get(i) or BookState(book=book, sleeve=i, cash=sl.cash)
            r.cash, r.holdings, r.entry = sl.cash, dict(sl.holdings), dict(sl.entry)
            if last_rebalance and i in last_rebalance:
                r.last_rebalance = last_rebalance[i]
            if params is not None:
                r.params = params
            s.add(r)
        s.commit()


def reset_book(book: str) -> None:
    with session() as s:
        for r in s.exec(select(BookState).where(BookState.book == book)):
            s.delete(r)
        for e in s.exec(select(EquityPoint).where(EquityPoint.book == book)):
            s.delete(e)
        s.commit()


def net_holdings(sleeves: list[Sleeve]) -> dict[str, float]:
    out: dict[str, float] = {}
    for sl in sleeves:
        for k, q in sl.holdings.items():
            out[k] = out.get(k, 0.0) + q
    return {k: q for k, q in out.items() if abs(q) > 1e-12}


def record_equity(book: str, day: date, **vals) -> None:
    with session() as s:
        e = s.exec(select(EquityPoint).where(EquityPoint.book == book, EquityPoint.day == day)).first()
        if e is None:
            e = EquityPoint(book=book, day=day, equity=vals.get("equity", 0.0))
        for k, v in vals.items():
            setattr(e, k, v)
        s.add(e)
        s.commit()


def equity_series(book: str) -> list[EquityPoint]:
    with session() as s:
        return list(s.exec(select(EquityPoint).where(EquityPoint.book == book).order_by(EquityPoint.day)))


def list_books(prefix: str = "") -> list[str]:
    with session() as s:
        return sorted({b for b in s.exec(select(BookState.book)) if b.startswith(prefix)})
