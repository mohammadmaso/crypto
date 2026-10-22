"""Turn (current net holdings -> target net holdings) into exchange orders.

Net long positions are held on the spot wallet, net shorts as isolated 1x margin sell positions. A coin that flips
sign gets two orders (close the old leg, open the new one). Orders are sequenced so that cash is freed first:
close longs -> close shorts -> open shorts -> open longs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..data.markets import MarketMeta

ORDER_SEQ = {"close_long": 0, "close_short": 1, "open_short": 2, "open_long": 3}


@dataclass
class PlannedOrder:
    symbol: str
    leg: str          # spot | margin
    side: str         # buy | sell
    action: str       # open_long | close_long | open_short | close_short
    amount: float     # coin units, rounded to market precision
    price: float      # signal (close) price
    full_close: bool = False

    @property
    def notional(self) -> float:
        return self.amount * self.price

    def dict(self) -> dict:
        return asdict(self) | {"notional": self.notional}


def _legs(sym: str, old: float, new: float) -> list[tuple[str, float, bool]]:
    """(action, coin amount, full_close) pieces for moving from old to new."""
    out: list[tuple[str, float, bool]] = []
    if old > 0 and new < old:
        out.append(("close_long", old - max(new, 0.0), new <= 0))
    if old < 0 and new > old:
        out.append(("close_short", -old - max(-new, 0.0), new >= 0))
    if new > 0 and new > max(old, 0.0):
        out.append(("open_long", new - max(old, 0.0), False))
    if new < 0 and -new > max(-old, 0.0):
        out.append(("open_short", -new - max(-old, 0.0), False))
    return out


def plan_orders(current: dict[str, float], target: dict[str, float], prices: dict[str, float], meta: MarketMeta,
                min_order_usdt: float) -> tuple[list[PlannedOrder], list[dict]]:
    orders: list[PlannedOrder] = []
    dropped: list[dict] = []
    floor = max(min_order_usdt, meta.min_order_usdt)
    for sym in sorted(set(current) | set(target)):
        old, new = current.get(sym, 0.0), target.get(sym, 0.0)
        px = prices.get(sym)
        if not px:
            dropped.append({"symbol": sym, "reason": "no price"})
            continue
        for action, qty, full in _legs(sym, old, new):
            amt = float(meta.round_amount(sym, qty))
            if amt <= 0 or amt * px < floor:
                dropped.append({"symbol": sym, "action": action, "amount": qty, "notional": qty * px,
                                "reason": f"below minimum order ({floor} USDT)"})
                continue
            side = "sell" if action in ("close_long", "open_short") else "buy"
            leg = "spot" if action.endswith("long") else "margin"
            orders.append(PlannedOrder(sym, leg, side, action, amt, px, full))
    orders.sort(key=lambda o: (ORDER_SEQ[o.action], o.symbol))
    return orders, dropped


def book_walk(levels: list[list], notional: float) -> tuple[float | None, float]:
    """Average fill price and filled notional when taking `notional` USDT from price levels [[price, qty], ...]."""
    filled = qty_total = 0.0
    for p, q in levels:
        p, q = float(p), float(q)
        take = min(q * p, notional - filled)
        filled += take
        qty_total += take / p
        if filled >= notional - 1e-9:
            break
    return (filled / qty_total if qty_total else None), filled


def slippage_of(book: dict, side: str, notional: float, ref: float) -> float | None:
    """Fractional slippage vs `ref` (signal price) when executing `notional` against the book; None = not enough depth."""
    levels = book.get("asks" if side == "buy" else "bids") or []
    avg, filled = book_walk(levels, notional)
    if avg is None or filled < notional * 0.999:
        return None
    return (avg / ref - 1) if side == "buy" else (1 - avg / ref)
