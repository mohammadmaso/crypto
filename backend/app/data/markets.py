"""Market metadata: precisions, minimum order value, margin (shortable) markets and their position fees."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from ..db import kv_get, kv_set
from ..nobitex.client import NobitexClient


@dataclass
class MarketMeta:
    amount_prec: dict[str, str] = field(default_factory=dict)    # BTCUSDT -> "0.000001"
    price_prec: dict[str, str] = field(default_factory=dict)
    min_order_usdt: float = 0.5
    margin: dict[str, dict] = field(default_factory=dict)        # BTCUSDT -> {sellEnabled, positionFeeRate, maxLeverage}
    fetched: float = 0.0

    def shortable(self) -> set[str]:
        return {s for s, m in self.margin.items() if s.endswith("USDT") and m.get("sellEnabled")}

    def position_fee(self, symbol: str, default: float) -> float:
        m = self.margin.get(symbol) or {}
        v = m.get("sellPositionFeeRate") or m.get("positionFeeRate")
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def round_amount(self, symbol: str, amount: float) -> Decimal:
        step = Decimal(self.amount_prec.get(symbol, "0.00000001"))
        return (Decimal(repr(abs(amount))) / step).to_integral_value(ROUND_DOWN) * step

    def round_price(self, symbol: str, price: float, up: bool) -> Decimal:
        step = Decimal(self.price_prec.get(symbol, "0.00000001"))
        q = Decimal(repr(price)) / step
        return (q.to_integral_value("ROUND_CEILING" if up else "ROUND_FLOOR")) * step


async def refresh(client: NobitexClient, max_age_s: float = 6 * 3600, force: bool = False) -> MarketMeta:
    cached = kv_get("market_meta")
    if cached and not force and time.time() - cached.get("fetched", 0) < max_age_s:
        return MarketMeta(**cached)
    opts = await client.options()
    margin = await client.margin_markets()
    meta = MarketMeta(
        amount_prec={k: str(v) for k, v in opts.get("amountPrecisions", {}).items()},
        price_prec={k: str(v) for k, v in opts.get("pricePrecisions", {}).items()},
        min_order_usdt=float(opts.get("minOrders", {}).get("usdt", 0.5)),
        margin=margin,
        fetched=time.time(),
    )
    kv_set("market_meta", meta.__dict__)
    return meta


def cached() -> MarketMeta:
    c = kv_get("market_meta")
    return MarketMeta(**c) if c else MarketMeta()
