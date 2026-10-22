"""Sanity check on YOUR real Binance research data: do different lookbacks pick different coins?
Run from the repo root:  cd backend && uv run python ../scripts/check_lookback.py"""
import sys
sys.path.insert(0, ".")
from app.backtest import binance_data
from app.config import StrategyParams
from app.strategy.signal import clean_glitches, features, targets_for

close, qv, fund = binance_data.load()
close = clean_glitches(close)
day = close.index[-30]
print("data:", close.shape, close.index.min().date(), "->", close.index.max().date(), "| checking", day.date())
for kw in ({"lookback": 7}, {"lookback": 14}, {"lookback": 30}, {"combo": True}):
    p = StrategyParams(**kw)
    t = targets_for(features(close, qv, p), fund.notna(), day, p)
    print(kw, "LONG", t.longs, "SHORT", t.shorts)
