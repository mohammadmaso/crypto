import pandas as pd

from app.api import main
from app.backtest import binance_data
from helpers import make_panel


def test_backtest_api_all_kinds(monkeypatch):
    close, qv = make_panel(n=300, seed=2)
    close.index = pd.date_range("2020-01-03", periods=300, freq="D")
    qv.index = close.index
    fund = close * 0 + 0.0001
    monkeypatch.setattr(binance_data, "available", lambda: True)
    monkeypatch.setattr(binance_data, "load", lambda *a, **k: (close, qv, fund))
    outs = {}
    for kind, params in [(None, {}), ("random", {}), ("btc_trend", {}), ("btc_hold", {}),
                         (None, {"combo": True}), (None, {"weighting": "invvol", "exposure": 0.6}),
                         (None, {"short_frac": 0, "long_frac": 1})]:
        o = main._backtest({"kind": kind or "strategy", "params": params, "costs": "nobitex"})
        assert o["stats"]["days"] > 0 and o["equity"] and o["btc"]
        outs[(kind, str(params))] = o
    assert outs[("btc_hold", "{}")]["label"] == "BTC buy & hold"


def test_backtest_api_macro_kinds(monkeypatch):
    import numpy as np
    from app.macro import data as md
    close, qv = make_panel(n=800, seed=2)
    close.index = pd.date_range("2020-01-03", periods=800, freq="D")
    qv.index = close.index
    rng = np.random.default_rng(0)
    macro = {"vix": pd.Series(20 + rng.normal(0, 1, 800).cumsum() * 0.1, index=close.index),
             "fng": pd.Series(np.clip(50 + rng.normal(0, 4, 800).cumsum(), 0, 100), index=close.index)}
    monkeypatch.setattr(binance_data, "available", lambda: True)
    monkeypatch.setattr(binance_data, "load", lambda *a, **k: (close, qv, close * 0 + 1e-4))
    monkeypatch.setattr(md, "load", lambda: macro)
    for kind in ("macro_rule", "macro_ml"):
        o = main._backtest({"kind": kind, "params": {}, "costs": "nobitex"})
        assert o["macro"]["features"] and o["equity"]
    assert "oos_auc" in o["macro"]
