"""SQLite persistence (SQLModel)."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import Column, UniqueConstraint
from sqlalchemy.types import JSON
from sqlmodel import Field, Session, SQLModel, create_engine, select

from .config import DB_PATH, Settings

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KV(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))


class Candle(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("symbol", "day"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    symbol: str = Field(index=True)          # e.g. BTCUSDT
    day: date = Field(index=True)            # Tehran trading day (candle opens 20:30 UTC the day before)
    o: float
    h: float
    l: float
    c: float
    v: float                                 # base volume
    q: float                                 # quote volume ~ v * c


class BookState(SQLModel, table=True):
    """One sleeve of one book. `book` = 'live' or 'shadow:<label>'. Holdings are signed coin quantities."""
    __table_args__ = (UniqueConstraint("book", "sleeve"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book: str = Field(index=True)
    sleeve: int
    cash: float
    holdings: dict = Field(default_factory=dict, sa_column=Column(JSON))
    entry: dict = Field(default_factory=dict, sa_column=Column(JSON))   # coin -> avg entry price
    last_rebalance: Optional[date] = None
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))


class EquityPoint(SQLModel, table=True):
    __table_args__ = (UniqueConstraint("book", "day"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    book: str = Field(index=True)
    day: date = Field(index=True)
    equity: float
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    fees: float = 0.0
    position_fees: float = 0.0
    gate: Optional[bool] = None
    gross: float = 0.0
    net: float = 0.0


class Run(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created: datetime = Field(default_factory=utcnow)
    day: date
    mode: str
    kind: str = "rebalance"                  # rebalance | kill | manual
    status: str = "started"                  # started|awaiting_approval|executed|skipped|blocked|failed|partial
    detail: dict = Field(default_factory=dict, sa_column=Column(JSON))


class OrderRec(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: Optional[int] = Field(default=None, index=True)
    created: datetime = Field(default_factory=utcnow)
    mode: str
    client_id: str = Field(index=True)
    symbol: str
    leg: str                                 # spot | margin
    side: str                                # buy | sell
    action: str = ""                         # open_long, close_long, open_short, close_short
    amount: float
    signal_price: float
    limit_price: Optional[float] = None
    status: str = "planned"                  # planned|approved|rejected|skipped|submitted|filled|partial|canceled|failed
    filled: float = 0.0
    avg_price: Optional[float] = None
    fee: float = 0.0
    exchange_id: Optional[str] = None
    error: Optional[str] = None


class Approval(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(index=True)
    created: datetime = Field(default_factory=utcnow)
    expires: datetime
    status: str = "pending"                  # pending|approved|rejected|expired|partial
    decisions: dict = Field(default_factory=dict, sa_column=Column(JSON))   # order_id -> bool
    telegram_message_id: Optional[int] = None


class Alert(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created: datetime = Field(default_factory=utcnow)
    level: str = "info"                      # info | warning | critical
    kind: str = ""
    message: str
    sent: bool = False


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def session() -> Session:
    return Session(engine, expire_on_commit=False)


def kv_get(key: str, default: Any = None) -> Any:
    with session() as s:
        row = s.get(KV, key)
        return default if row is None else row.value


def kv_set(key: str, value: Any) -> None:
    with session() as s:
        row = s.get(KV, key)
        if row is None:
            s.add(KV(key=key, value=value))
        else:
            row.value = value
            s.add(row)
        s.commit()


def load_settings() -> Settings:
    raw = kv_get("settings")
    return Settings.model_validate(raw) if raw else Settings()


def save_settings(st: Settings) -> None:
    kv_set("settings", json.loads(st.model_dump_json()))


def add_alert(level: str, kind: str, message: str) -> Alert:
    with session() as s:
        a = Alert(level=level, kind=kind, message=message)
        s.add(a)
        s.commit()
        return a


def latest_alerts(n: int = 100) -> list[Alert]:
    with session() as s:
        return list(s.exec(select(Alert).order_by(Alert.id.desc()).limit(n)))
