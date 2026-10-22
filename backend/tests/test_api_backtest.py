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
