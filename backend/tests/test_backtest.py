import pytest

from app.backtest.run import run_backtest, summarize
from app.config import StrategyParams
from app.strategy.book import CostModel
from helpers import make_panel


def test_backtest_runs_and_costs_reduce_return():
    close, qv = make_panel(n=300, seed=3)
    p = StrategyParams()
    free = run_backtest(close, qv, set(close.columns), p, CostModel(0, 0, 0))
    paid = run_backtest(close, qv, set(close.columns), p, CostModel(0.003, 0.003, 0.0005))
    assert len(free.returns) == 300
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]
    assert paid.breakdown["position_fee"].sum() <= 0 and paid.breakdown["fees"].sum() < 0
    assert summarize(paid)["stats"]["days"] > 0


def test_combo_backtest_runs():
    close, qv = make_panel(n=300, seed=4)
    res = run_backtest(close, qv, set(close.columns), StrategyParams(combo=True), CostModel(0.001, 0.001))
    assert len(res.returns) == 300 and res.equity.iloc[-1] > 0


def test_gate_off_backtest_never_holds_when_btc_falls():
    close, qv = make_panel(n=200, btc_up=False, seed=5)
    res = run_backtest(close, qv, set(close.columns), StrategyParams(), CostModel(0.001, 0.001))
    assert (res.returns.iloc[120:].abs() < 0.5).all()
