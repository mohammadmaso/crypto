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


def test_new_setups_run():
    from app.backtest.run import btc_baseline, random_median
    close, qv = make_panel(n=300, seed=8)
    sh, cost = set(close.columns), CostModel(0.001, 0.001, 0.0005)
    base = run_backtest(close, qv, sh, StrategyParams(), cost)
    for upd in ({"weighting": "invvol"}, {"exposure": 0.6}, {"short_frac": 0.0, "long_frac": 1.0}, {"gate_on": False}):
        r = run_backtest(close, qv, sh, StrategyParams(**upd), cost)
        assert len(r.returns) == 300
    long_only = run_backtest(close, qv, sh, StrategyParams(short_frac=0.0, long_frac=1.0), cost)
    assert long_only.breakdown["short_pnl"].abs().sum() == 0
    half = run_backtest(close, qv, sh, StrategyParams(exposure=0.5), cost)
    assert half.returns.std() < base.returns.std()                    # lower exposure -> lower volatility
    hold = btc_baseline(close, StrategyParams(), cost, trend=False)
    trend = btc_baseline(close, StrategyParams(), cost, trend=True)
    assert len(hold.returns) == len(trend.returns) == 300
    assert (hold.returns.iloc[1:] == close["BTCUSDT"].pct_change().iloc[1:]).all()
    assert len(random_median(close, qv, sh, StrategyParams(), cost, seeds=3).returns) == 300


def test_invvol_weights_sum_to_leg_fraction():
    from app.strategy.signal import features, targets_for
    close, qv = make_panel(n=200, seed=9)
    p = StrategyParams(weighting="invvol")
    f = features(close, qv, p)
    f.gate[:] = True
    t = targets_for(f, set(close.columns), close.index[-1], p)
    assert abs(sum(w for w in t.weights.values() if w > 0) - 0.5) < 1e-9
    assert len({round(w, 6) for w in t.weights.values() if w > 0}) > 1   # not equal weights
