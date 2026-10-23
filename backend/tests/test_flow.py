"""Approval timeout, catch-up window, sleeve netting and the paper-mode end-to-end run."""
import asyncio
from datetime import timedelta

import pytest

from app import books
from app.config import StrategyParams
from app.data.candles import day_open_ts, last_closed_day
from app.db import Approval, OrderRec, Run, load_settings, save_settings, session, utcnow
from app.engine import Engine
from app.scheduler import catchup_due
from app.strategy.book import CostModel, Sleeve, rebalance


def test_catchup_window():
    d = last_closed_day()
    closed_at = day_open_ts(d) + 86400
    assert catchup_due(None, 12, now=closed_at + 3600)
    assert not catchup_due(str(d), 12, now=closed_at + 3600)       # already ran
    assert not catchup_due(None, 12, now=closed_at + 13 * 3600)    # too late: skip


def test_rebalance_books_fees_and_net_holdings():
    sl = Sleeve(cash=900.0)
    cost = CostModel(long_side=0.002, short_side=0.002)
    prices = {"A": 100.0, "B": 50.0}
    sl2 = sl
    rebalance(sl2, {"A": 1.0, "B": -2.0}, prices, cost)
    assert sl2.equity(prices) == pytest.approx(900.0 - (100 * 0.002 + 100 * 0.002))
    assert books.net_holdings([sl2, Sleeve(cash=0, holdings={"A": 1.0})]) == {"A": 2.0, "B": -2.0}


def _seed_run_with_approval(minutes_left: int):
    with session() as s:
        run = Run(day=last_closed_day(), mode="paper", status="awaiting_approval", detail={"desired": {"0": {"AUSDT": 1.0}}})
        s.add(run)
        s.commit()
        s.add(OrderRec(run_id=run.id, mode="paper", client_id="x", symbol="AUSDT", leg="spot", side="buy",
                       action="open_long", amount=1.0, signal_price=10.0))
        a = Approval(run_id=run.id, expires=utcnow() + timedelta(minutes=minutes_left))
        s.add(a)
        s.commit()
        return run.id, a.id


def test_approval_timeout_skips_and_marks_sleeves_pending():
    run_id, aid = _seed_run_with_approval(-1)
    eng = Engine()
    asyncio.run(eng.approval_tick())
    with session() as s:
        assert s.get(Approval, aid).status == "expired"
        assert s.get(Run, run_id).status == "skipped"
        assert s.get(OrderRec, 1).status == "skipped"
    assert 0 in eng.state()["pending"]       # retried next cycle


def test_approval_reject_and_pending_not_expired():
    run_id, aid = _seed_run_with_approval(60)
    eng = Engine()
    asyncio.run(eng.approval_tick())
    with session() as s:
        assert s.get(Approval, aid).status == "pending"
    eng.decide(aid, False)
    asyncio.run(eng.approval_tick())
    with session() as s:
        assert s.get(Approval, aid).status == "rejected"
        assert s.get(Run, run_id).status == "skipped"


def test_settings_roundtrip_defaults_paper():
    st = load_settings()
    assert st.mode == "paper" and st.strategy.lookback == 14 and st.strategy.k == 5
    st.capital_usdt = 500
    save_settings(st)
    assert load_settings().capital_usdt == 500
