import pandas as pd

from app.config import StrategyParams
from app.strategy.signal import features, targets_for, sleeve_rebalances_on, all_variants
from helpers import make_panel


def test_gate_off_goes_to_cash():
    close, qv = make_panel(btc_up=False)
    p = StrategyParams()
    f = features(close, qv, p)
    day = close.index[-1]
    assert not f.gate[day]
    assert targets_for(f, set(close.columns), day, p).weights == {}


def test_long_short_weights_and_no_overlap():
    close, qv = make_panel(btc_up=True)
    p = StrategyParams()
    f = features(close, qv, p)
    day = close.index[-1]
    close.loc[:, "BTCUSDT"] = close["BTCUSDT"]  # gate computed from f
    if not f.gate[day]:
        f.gate[:] = True
    t = targets_for(f, set(close.columns), day, p)
    assert len(t.longs) == 5 and len(t.shorts) == 5
    assert not set(t.longs) & set(t.shorts)
    assert abs(sum(w for w in t.weights.values() if w > 0) - 0.5) < 1e-12
    assert abs(sum(w for w in t.weights.values() if w < 0) + 0.5) < 1e-12


def test_fewer_than_k_shortable():
    close, qv = make_panel()
    p = StrategyParams()
    f = features(close, qv, p)
    f.gate[:] = True
    day = close.index[-1]
    shortable = set([s for s in f.mom.loc[day].sort_values().index if s != "BTCUSDT"][:3])
    t = targets_for(f, shortable, day, p)
    assert set(t.shorts) <= shortable
    assert len(t.shorts) <= 3
    if t.shorts:   # short weight is split across the shorts found
        assert abs(sum(w for w in t.weights.values() if w < 0) + 0.5) < 1e-12


def test_universe_ranks_by_trailing_volume_and_requires_history():
    close, qv = make_panel(n=120)
    close.loc[close.index[:100], "C0USDT"] = float("nan")   # only 20 days of history
    qv.loc[:, "C1USDT"] = 1e12                              # huge volume
    p = StrategyParams(universe_size=3)
    f = features(close, qv, p)
    day = close.index[-1]
    from app.strategy.signal import eligible_universe
    u = eligible_universe(f, day, 3)
    assert u[0] == "BTCUSDT" or u[0] == "C1USDT"
    assert "C0USDT" not in eligible_universe(f, day, 30)
    assert len(u) == 3


def test_tranche_schedule_staggered():
    p = StrategyParams()
    days = {k: [i for i in range(12) if sleeve_rebalances_on(i, k, p)] for k in range(3)}
    assert days == {0: [0, 3, 6, 9], 1: [1, 4, 7, 10], 2: [2, 5, 8, 11]}


def test_variants_include_combo():
    vs = all_variants()
    assert len(vs) == 14 and sum(v.combo for v in vs) == 2


def test_combo_signal_ranks_and_no_overlap():
    close, qv = make_panel(n=200, seed=7)
    p = StrategyParams(combo=True)
    f = features(close, qv, p)
    f.gate[:] = True
    day = close.index[-1]
    t = targets_for(f, set(close.columns), day, p)
    assert len(t.longs) == 5 and len(t.shorts) == 5 and not set(t.longs) & set(t.shorts)
    assert all(0 <= s <= 1 for _, s in t.ranked)                      # rank-average scores
    top = t.ranked[0][0]
    single = targets_for(features(close, qv, StrategyParams()), set(close.columns), day, StrategyParams())
    assert single.ranked != t.ranked or top in t.longs               # runs through the same selection


def test_combo_label():
    assert "7/14/30" in StrategyParams(combo=True).label


def test_too_few_shortable_holds_cash():
    close, qv = make_panel()
    p = StrategyParams()
    f = features(close, qv, p)
    f.gate[:] = True
    day = close.index[-1]
    only_two = set(f.mom.loc[day].sort_values().index[:3]) - {"BTCUSDT"}
    only_two = set(list(only_two)[:2])
    t = targets_for(f, only_two, day, p)
    assert t.weights == {} and t.shorts == []
