import numpy as np
import pandas as pd


def make_panel(n=160, coins=12, seed=1, btc_up=True):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    cols = ["BTCUSDT"] + [f"C{i}USDT" for i in range(coins)]
    drift = 0.002 if btc_up else -0.003
    close = pd.DataFrame(100 * np.cumprod(1 + rng.normal(drift, 0.02, (n, len(cols))), axis=0), index=idx, columns=cols)
    qv = pd.DataFrame(rng.uniform(1e6, 1e7, (n, len(cols))), index=idx, columns=cols)
    qv["BTCUSDT"] = 1e9
    return close, qv
