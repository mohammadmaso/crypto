"""Brokers behind one interface. PaperBroker simulates fills on live Nobitex order books; NobitexBroker trades
spot (longs) and isolated 1x margin positions (shorts) on testnet or mainnet."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from ..config import Settings
from ..data.markets import MarketMeta
from ..nobitex.client import NobitexClient, NobitexError
from .planner import PlannedOrder, book_walk

log = logging.getLogger(__name__)


@dataclass
class ExecResult:
    status: str                 # filled | partial | failed | skipped
    filled: float = 0.0         # coin units
    avg_price: float | None = None
    fee: float = 0.0            # USDT
    exchange_id: str | None = None
    error: str | None = None
    cash_delta: float | None = None   # USDT change to the book; None = standard (qty * price -/+ fee)

    def book_cash_delta(self, side: str) -> float:
        if self.cash_delta is not None:
            return self.cash_delta
        gross = self.filled * (self.avg_price or 0.0)
        return (-gross if side == "buy" else gross) - self.fee


def new_client_id(prefix: str = "nm") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def src_of(symbol: str) -> str:
    return symbol[:-4].lower()


class Broker:
    name = "base"

    async def execute(self, order: PlannedOrder, client_id: str) -> ExecResult:  # pragma: no cover
        raise NotImplementedError

    async def reconcile(self, net: dict[str, float]) -> list[str]:
        return []

    async def prepare_cash(self, orders: list[PlannedOrder]) -> None:
        return None

    async def account_summary(self) -> dict:
        return {}


class PaperBroker(Broker):
    """Fills each order by walking the live (mainnet) order book; charges the configured fees."""
    name = "paper"

    def __init__(self, market: NobitexClient, settings: Settings):
        self.market = market
        self.st = settings

    async def execute(self, order: PlannedOrder, client_id: str) -> ExecResult:
        avg = None
        try:
            book = await self.market.orderbook(order.symbol)
            levels = book.get("asks" if order.side == "buy" else "bids") or []
            avg, filled = book_walk(levels, order.notional)
            if avg is None or filled < order.notional * 0.999:
                avg = None
        except NobitexError as e:
            log.warning("paper: orderbook %s failed: %s", order.symbol, e)
        if avg is None:   # thin or missing book: fill at signal price with modelled slippage
            slip = self.st.costs.slippage
            avg = order.price * (1 + slip if order.side == "buy" else 1 - slip)
        rate = self.st.costs.spot_taker_fee if order.leg == "spot" else self.st.costs.margin_taker_fee
        return ExecResult(status="filled", filled=order.amount, avg_price=avg, fee=order.amount * avg * rate,
                          exchange_id=f"paper-{client_id}")


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


class NobitexBroker(Broker):
    """Real orders. Longs: spot. Shorts: margin sell positions at the configured leverage (1x)."""

    def __init__(self, client: NobitexClient, settings: Settings, meta: MarketMeta, name: str):
        self.c = client
        self.st = settings
        self.meta = meta
        self.name = name

    # ----- account -----
    async def _spot_balances(self) -> dict[str, float]:
        return {w["currency"]: _f(w.get("balance")) for w in await self.c.wallets("spot")}

    async def _margin_usdt_free(self) -> float:
        for w in await self.c.wallets("margin"):
            if w.get("currency") == "usdt":
                return _f(w.get("activeBalance", w.get("balance")))
        return 0.0

    async def _short_positions(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for p in await self.c.positions("active"):
            if p.get("side") == "sell" and p.get("dstCurrency") == "usdt":
                out.setdefault(f"{p['srcCurrency'].upper()}USDT", []).append(p)
        return out

    async def account_summary(self) -> dict:
        spot = await self._spot_balances()
        shorts = await self._short_positions()
        return {
            "spot": {k: v for k, v in spot.items() if v > 0},
            "margin_usdt_free": await self._margin_usdt_free(),
            "shorts": {s: sum(_f(p.get("liability")) for p in ps) for s, ps in shorts.items()},
        }

    async def reconcile(self, net: dict[str, float]) -> list[str]:
        """The bot's book must be covered by the exchange: longs by spot balances, shorts by open positions."""
        issues: list[str] = []
        spot = await self._spot_balances()
        shorts = await self._short_positions()
        for sym, q in net.items():
            src = src_of(sym)
            if q > 0:
                have = spot.get(src, 0.0)
                if have < q * 0.99:
                    issues.append(f"{sym}: book long {q:.8g} but spot balance {have:.8g}")
            else:
                have = sum(_f(p.get("liability")) for p in shorts.get(sym, []))
                if abs(have - (-q)) > max(1e-9, -q * 0.01):
                    issues.append(f"{sym}: book short {-q:.8g} but open short liability {have:.8g}")
        for sym, ps in shorts.items():
            if sym not in net or net[sym] >= 0:
                issues.append(f"{sym}: exchange has an open short the bot did not open ({len(ps)} position(s))")
        return issues

    async def prepare_cash(self, orders: list[PlannedOrder]) -> None:
        """Move USDT collateral to the margin wallet for new shorts (collateral = notional / leverage, +1%)."""
        need = sum(o.notional for o in orders if o.action == "open_short") / max(self.st.risk.short_leverage, 1.0)
        if need <= 0:
            return
        free = await self._margin_usdt_free()
        gap = need * 1.01 - free
        if gap > 0:
            spot_usdt = (await self._spot_balances()).get("usdt", 0.0)
            amt = min(gap, spot_usdt)
            if amt > 0:
                await self.c.transfer("usdt", f"{amt:.2f}", "spot", "margin")

    async def ensure_spot_usdt(self, need: float) -> None:
        have = (await self._spot_balances()).get("usdt", 0.0)
        if have >= need:
            return
        free = await self._margin_usdt_free()
        amt = min(need * 1.01 - have, free)
        if amt > 0:
            await self.c.transfer("usdt", f"{amt:.2f}", "margin", "spot")

    # ----- orders -----
    async def execute(self, order: PlannedOrder, client_id: str) -> ExecResult:
        try:
            if order.action == "close_short":
                return await self._close_short(order, client_id)
            if order.action == "open_long":
                await self.ensure_spot_usdt(order.notional * 1.01)
            return await self._work(order, client_id)
        except NobitexError as e:
            log.error("order %s %s failed: %s", order.action, order.symbol, e)
            return ExecResult(status="failed", error=str(e))

    async def _best(self, symbol: str, side: str) -> float | None:
        b = await self.c.orderbook(symbol)
        lv = b.get("asks" if side == "buy" else "bids") or []
        return float(lv[0][0]) if lv else None

    async def _place(self, order: PlannedOrder, amount: float, execution: str, price: float | None, cid: str) -> dict:
        body = dict(type=order.side, execution=execution, srcCurrency=src_of(order.symbol), dstCurrency="usdt",
                    amount=str(self.meta.round_amount(order.symbol, amount)), clientOrderId=cid)
        if price is not None:
            body["price"] = str(self.meta.round_price(order.symbol, price, up=order.side == "buy"))
        if order.leg == "margin":
            body["leverage"] = str(self.st.risk.short_leverage)
            return await self.c.add_margin_order(**body)
        return await self.c.add_order(**body)

    async def _wait(self, oid, seconds: int) -> dict:
        st: dict = {}
        for _ in range(max(1, seconds // 2)):
            await asyncio.sleep(2)
            st = await self.c.order_status(order_id=oid)
            if str(st.get("status", "")).lower() in ("done", "canceled", "cancelled"):
                break
        return st

    async def _work(self, order: PlannedOrder, client_id: str) -> ExecResult:
        """Limit at best +- offset, re-placed up to N times; then market for the remainder."""
        ex = self.st.execution
        remaining, cost, fee, filled, last_id = order.amount, 0.0, 0.0, 0.0, None
        fee_coin = 0.0   # spot buys pay the fee in the coin, so the book receives less
        attempts = ex.limit_retries if ex.order_style == "limit_then_market" else 0
        for i in range(attempts + 1):
            final = i == attempts
            if remaining * order.price < self.meta.min_order_usdt:
                break
            cid = f"{client_id}-{i}"
            if final:
                o = await self._place(order, remaining, "market", None, cid)
            else:
                best = await self._best(order.symbol, order.side)
                if best is None:
                    continue
                px = best * (1 + ex.limit_offset if order.side == "buy" else 1 - ex.limit_offset)
                o = await self._place(order, remaining, "limit", px, cid)
            last_id = o.get("id")
            st = await self._wait(last_id, 10 if final else ex.limit_wait_seconds)
            if not final and str(st.get("status", "")).lower() not in ("done",):
                try:
                    await self.c.cancel_order(order_id=last_id)
                except NobitexError:
                    pass
                st = await self.c.order_status(order_id=last_id)
            m = _f(st.get("matchedAmount"))
            if m > 0:
                filled += m
                cost += m * _f(st.get("averagePrice"), order.price)
                fee += _fee_usdt(st, order.side, _f(st.get("averagePrice"), order.price))
                if order.side == "buy" and order.leg == "spot":
                    fee_coin += _f(st.get("fee"))
            remaining = order.amount - filled
            if remaining <= 0 or str(st.get("status", "")).lower() == "done" and remaining * order.price < 1:
                break
        if filled <= 0:
            return ExecResult(status="failed", exchange_id=str(last_id) if last_id else None, error="no fill")
        status = "filled" if filled >= order.amount * 0.995 else "partial"
        if order.side == "buy":
            cash = -cost if order.leg == "spot" else -cost - fee
        else:
            cash = cost - fee
        return ExecResult(status=status, filled=filled - fee_coin, avg_price=cost / filled, fee=fee,
                          exchange_id=str(last_id), cash_delta=cash)

    async def _close_short(self, order: PlannedOrder, client_id: str) -> ExecResult:
        positions = (await self._short_positions()).get(order.symbol, [])
        if not positions:
            return ExecResult(status="failed", error="no open short position")
        remaining, filled, cost, fee = order.amount, 0.0, 0.0, 0.0
        for p in sorted(positions, key=lambda p: -_f(p.get("liability"))):
            if remaining <= 0:
                break
            liab = _f(p.get("liability")) - _f(p.get("liabilityInOrder"))
            amt = liab if order.full_close else min(liab, remaining)
            amount_str = f"{amt:.10f}".rstrip("0").rstrip(".") if order.full_close else str(self.meta.round_amount(order.symbol, amt))
            r = await self.c.close_position(int(p["id"]), execution="market", amount=amount_str,
                                            clientOrderId=f"{client_id}-{p['id']}")
            o = r.get("order") or {}
            oid = o.get("id")
            st = await self._wait(oid, 10) if oid else o
            m = _f(st.get("matchedAmount"), amt)
            avg = _f(st.get("averagePrice"), order.price)
            filled += m
            cost += m * avg
            fee += _fee_usdt(st, "buy", avg)
            remaining -= m
        if filled <= 0:
            return ExecResult(status="failed", error="close produced no fill")
        return ExecResult(status="filled" if remaining <= order.amount * 0.005 else "partial",
                          filled=filled, avg_price=cost / filled, fee=fee)


def _fee_usdt(st: dict, side: str, avg: float) -> float:
    """Nobitex reports the fee in the received currency (coin for buys, USDT for sells)."""
    f = _f(st.get("fee"))
    return f * avg if side == "buy" else f
