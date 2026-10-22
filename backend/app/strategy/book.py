"""Sleeve/book accounting shared by the backtest, the shadow books and the paper/live bookkeeping.

A sleeve holds signed coin quantities plus USDT cash. Shorts are booked with their sale proceeds in cash, so
equity = cash + sum(qty * price). Gross exposure 100% means |longs| + |shorts| = equity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..config import Costs


@dataclass
class CostModel:
    long_side: float        # per-side cost on the spot (long) leg, fee + slippage
    short_side: float       # per-side cost on the short leg
    short_carry_daily: float = 0.0   # daily cost on short notional (Nobitex margin position fee); funding is separate

    @classmethod
    def binance_research(cls) -> "CostModel":
        return cls(long_side=0.0015, short_side=0.0010)

    @classmethod
    def nobitex(cls, c: Costs) -> "CostModel":
        return cls(long_side=c.spot_taker_fee + c.slippage, short_side=c.margin_taker_fee + c.slippage,
                   short_carry_daily=c.position_fee_daily)


@dataclass
class Sleeve:
    cash: float
    holdings: dict[str, float] = field(default_factory=dict)
    entry: dict[str, float] = field(default_factory=dict)

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + sum(q * prices[s] for s, q in self.holdings.items())

    def exposure(self, prices: dict[str, float]) -> tuple[float, float]:
        lo = sum(q * prices[s] for s, q in self.holdings.items() if q > 0)
        sh = sum(-q * prices[s] for s, q in self.holdings.items() if q < 0)
        return lo, sh


@dataclass
class Fill:
    symbol: str
    qty: float          # signed change
    price: float
    fee: float
    leg: str            # spot | margin


def leg_split(old: float, new: float) -> tuple[float, float]:
    """|change| on the long leg and on the short leg when moving a position from old to new."""
    d_long = abs(max(new, 0.0) - max(old, 0.0))
    d_short = abs(min(new, 0.0) - min(old, 0.0))
    return d_long, d_short


def target_quantities(weights: dict[str, float], equity: float, prices: dict[str, float]) -> dict[str, float]:
    return {s: w * equity / prices[s] for s, w in weights.items() if prices.get(s) and prices[s] > 0}


def rebalance(sl: Sleeve, target_qty: dict[str, float], prices: dict[str, float], cost: CostModel,
              exec_prices: dict[str, float] | None = None) -> list[Fill]:
    """Trade the sleeve to `target_qty` (everything else to zero). Fees come out of cash."""
    fills: list[Fill] = []
    px = exec_prices or prices
    for s in sorted(set(sl.holdings) | set(target_qty)):
        old = sl.holdings.get(s, 0.0)
        new = target_qty.get(s, 0.0)
        if math.isclose(old, new, rel_tol=1e-12, abs_tol=1e-15):
            continue
        p = px.get(s) or prices[s]
        d_long, d_short = leg_split(old, new)
        fee = (d_long * cost.long_side + d_short * cost.short_side) * p
        sl.cash -= (new - old) * p + fee
        if abs(new) < 1e-15:
            sl.holdings.pop(s, None)
            sl.entry.pop(s, None)
        else:
            if old == 0 or (old > 0) != (new > 0):
                sl.entry[s] = p
            elif abs(new) > abs(old):
                sl.entry[s] = (sl.entry.get(s, p) * abs(old) + p * (abs(new) - abs(old))) / abs(new)
            sl.holdings[s] = new
        fills.append(Fill(symbol=s, qty=new - old, price=p, fee=fee, leg="spot" if d_long >= d_short else "margin"))
    return fills


def apply_short_carry(sl: Sleeve, prices: dict[str, float], cost: CostModel,
                      funding: dict[str, float] | None = None) -> tuple[float, float]:
    """Daily carry on shorts. funding[s] > 0 means shorts receive (Binance perps). Returns (funding, position_fee)."""
    fund = fee = 0.0
    for s, q in sl.holdings.items():
        if q >= 0:
            continue
        notional = -q * prices[s]
        if funding and s in funding and funding[s] == funding[s]:
            fund += funding[s] * notional
        fee += cost.short_carry_daily * notional
    sl.cash += fund - fee
    return fund, fee
