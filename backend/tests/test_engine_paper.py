"""Whole daily run in paper mode on a synthetic market (no network)."""
import pandas as pd

from app import books
from app.data.markets import MarketMeta
from app.db import Run, OrderRec, load_settings, save_settings, session
from app.engine import ENGINE, Engine, MarketSnapshot
from app.data.candles import last_closed_day
from helpers import make_panel


class FakeMarket:
    async def orderbook(self, sym):
        return {"bids": [["99.97", "1e9"]], "asks": [["100.03", "1e9"]]}

    async def close(self):
        pass


def _snap(btc_up=True):
    close, qv = make_panel(n=200, btc_up=btc_up)
    d = last_closed_day()
    close.index = pd.date_range(end=pd.Timestamp(d), periods=len(close), freq="D")
    qv.index = close.index
    px = close.iloc[-1].to_dict()
    meta = MarketMeta(margin={c: {"sellEnabled": True, "positionFeeRate": "0.0005"} for c in close.columns},
                      min_order_usdt=0.5)
    return MarketSnapshot(day=d, close=close, qvol=qv, prices=px, meta=meta, shortable=meta.shortable(), age_hours=1.0)


async def _run(monkeypatch, snap):
    eng = Engine()
    async def fake_snapshot(self, st, market, sync=True):
        return snap
    monkeypatch.setattr(Engine, "snapshot", fake_snapshot)
    monkeypatch.setattr(Engine, "market_client", lambda self, st: FakeMarket())
    return eng, await eng.daily(force=True)


async def test_paper_run_trades_and_is_idempotent(monkeypatch):
    snap = _snap(btc_up=True)
    eng, res = await _run(monkeypatch, snap)
    assert res["status"] in ("executed", "already_ran"), res
    sleeves = books.load_book(books.LIVE, 3, 1000.0)
    st = load_settings()
    gate = bool(books.equity_series(books.LIVE)[-1].gate)
    if gate:
        net = books.net_holdings(sleeves)
        assert any(q > 0 for q in net.values()) and any(q < 0 for q in net.values())
        # per-coin cap: <= 25% of a sleeve's equity
        for sl in sleeves:
            for s, q in sl.holdings.items():
                assert abs(q * snap.prices[s]) <= 0.25 * 1000 / 3 * 1.05
    with session() as s:
        assert s.query(Run).count() >= 1


async def test_gate_off_holds_cash(monkeypatch):
    snap = _snap(btc_up=False)
    eng, res = await _run(monkeypatch, snap)
    assert books.net_holdings(books.load_book(books.LIVE, 3, 1000.0)) == {}


async def test_stale_data_blocks(monkeypatch):
    snap = _snap()
    snap.close = snap.close.iloc[:-3]
    snap.age_hours = 100
    eng, res = await _run(monkeypatch, snap)
    assert res["status"] == "blocked"
    assert books.net_holdings(books.load_book(books.LIVE, 3, 1000.0)) == {}


async def test_kill_flattens_and_pauses(monkeypatch):
    snap = _snap(btc_up=True)
    eng, _ = await _run(monkeypatch, snap)
    await eng._kill_locked(load_settings(), FakeMarket(), snap, "test")
    assert books.net_holdings(books.load_book(books.LIVE, 3, 1000.0)) == {}
    assert load_settings().paused
