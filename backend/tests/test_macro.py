import numpy as np
import pandas as pd

from app.config import StrategyParams
from app.macro import data as md
from app.macro.features import build, expanding_z
from app.macro.regime import FLOOR, ml_overlay, rule_flags, rule_overlay
from app.strategy.signal import features
from helpers import make_panel
from app.backtest.run import run_backtest
from app.strategy.book import CostModel


def _macro(idx, seed=0):
    rng = np.random.default_rng(seed)
    n = len(idx)
    return {"vix": pd.Series(20 + rng.normal(0, 3, n).cumsum() * 0.1, index=idx),
            "dxy": pd.Series(100 + rng.normal(0, 0.2, n).cumsum(), index=idx),
            "y10": pd.Series(4 + rng.normal(0, 0.02, n).cumsum(), index=idx),
            "fng": pd.Series(np.clip(50 + rng.normal(0, 5, n).cumsum(), 0, 100), index=idx)}


def test_parsers():
    s = md.parse_fred("observation_date,VIXCLS\n2024-01-02,13.2\n2024-01-03,.\n2024-01-04,14.1\n")
    assert list(s.values) == [13.2, 14.1]
    f = md.parse_fng({"data": [{"value": "25", "timestamp": "1704153600"}, {"value": "70", "timestamp": "1704067200"}]})
    assert list(f.values) == [70.0, 25.0]


def test_expanding_z_has_no_lookahead():
    x = pd.Series(np.arange(400, dtype=float))
    z1 = expanding_z(x)
    x2 = x.copy(); x2.iloc[300:] = 1e6                       # change the future
    assert np.allclose(z1.iloc[:300].dropna(), expanding_z(x2).iloc[:300].dropna())


def test_features_lag_macro_one_day():
    close, qv = make_panel(n=260, seed=1)
    f = features(close, qv, StrategyParams())
    macro = {"vix": pd.Series(np.arange(260, dtype=float), index=close.index)}
    feat = build(f, macro)
    assert feat["vix"].iloc[10] == 9.0                       # yesterday's value
    assert {"btc_trend", "btc_vol", "breadth", "dispersion"} <= set(feat.columns)
    assert feat["breadth"].dropna().between(0, 1).all()


def test_rule_overlay_cuts_exposure_in_stress():
    close, qv = make_panel(n=500, seed=3)
    f = features(close, qv, StrategyParams())
    macro = _macro(close.index)
    macro["vix"].iloc[-5:] += 100                            # sudden VIX spike at the end
    feat = build(f, macro)
    expo = rule_overlay(feat)
    assert expo.between(FLOOR, 1.0).all()
    assert expo.iloc[-1] < 1.0 and rule_flags(feat)["VIX spike"].iloc[-1]
    assert expo.iloc[:150].eq(1.0).all()                     # not enough history for z-scores -> neutral


def test_ml_overlay_walk_forward_and_backtest_integration():
    close, qv = make_panel(n=800, seed=5)
    p = StrategyParams()
    f = features(close, qv, p)
    f.gate[:] = True
    feat = build(f, _macro(close.index, 1))
    cost = CostModel(0.001, 0.001, 0.0005)
    base = run_backtest(close, qv, set(close.columns), p, cost, feats=f)
    expo, diag = ml_overlay(feat, base.returns, base.gate)
    assert expo.between(FLOOR, 1.0).all()
    assert expo.iloc[:365].eq(1.0).all()                     # no model before min_train
    assert diag["scored_days"] > 100
    res = run_backtest(close, qv, set(close.columns), p, cost, feats=f, overlay=expo)
    assert len(res.returns) == 800


def test_ml_overlay_ignores_future(monkeypatch):
    """Scores for day t must not change if data after t changes (only labels >= horizon days old are used)."""
    close, qv = make_panel(n=700, seed=6)
    p = StrategyParams()
    f = features(close, qv, p)
    f.gate[:] = True
    feat = build(f, _macro(close.index, 2))
    base = run_backtest(close, qv, set(close.columns), p, CostModel(0.001, 0.001), feats=f)
    e1, _ = ml_overlay(feat, base.returns, base.gate)
    r2 = base.returns.copy(); r2.iloc[600:] = 0.5            # rewrite the future returns
    e2, _ = ml_overlay(feat, r2, base.gate)
    assert np.allclose(e1.iloc[:600 - 30], e2.iloc[:600 - 30])
