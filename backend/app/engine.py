"""Daily orchestration: data -> risk checks -> targets -> (approval) -> execution -> bookkeeping -> reports."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Awaitable, Callable

import pandas as pd
from sqlmodel import select

from . import books
from .books import LIVE
from .config import MAINNET_URL, Settings, base_url, secrets_for
from .data import candles, markets
from .data.candles import last_closed_day
from .db import Approval, OrderRec, Run, add_alert, kv_get, kv_set, load_settings, save_settings, session, utcnow
from .exec.broker import Broker, NobitexBroker, PaperBroker, new_client_id
from .exec.planner import PlannedOrder, plan_orders, slippage_of
from .nobitex.client import NobitexClient, NobitexError
from .strategy.book import Sleeve
from .strategy.signal import Features, clean_glitches, features, targets_for

log = logging.getLogger(__name__)

Notifier = Callable[..., Awaitable[None]]


async def _noop(*a, **k) -> None:
    return None


@dataclass
class MarketSnapshot:
    day: date
    close: pd.DataFrame
    qvol: pd.DataFrame
    prices: dict[str, float]
    meta: markets.MarketMeta
    shortable: set[str]
    age_hours: float


class Engine:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.notify: Notifier = _noop             # (text, kind="info", approval_id=None)
        self.last_snapshot: MarketSnapshot | None = None

    # ---------------- clients / brokers ----------------
    def market_client(self, st: Settings) -> NobitexClient:
        return NobitexClient(MAINNET_URL, secrets_for("live"), proxy=st.nobitex_proxy_url or None)

    def broker(self, st: Settings, market: NobitexClient, meta: markets.MarketMeta) -> tuple[Broker, NobitexClient | None]:
        if st.mode == "paper":
            return PaperBroker(market, st), None
        c = NobitexClient(base_url(st.mode), secrets_for(st.mode), proxy=st.nobitex_proxy_url or None)
        return NobitexBroker(c, st, meta, st.mode), c

    # ---------------- data ----------------
    async def snapshot(self, st: Settings, market: NobitexClient, sync: bool = True) -> MarketSnapshot:
        meta = await markets.refresh(market)
        if sync:
            syms = sorted(set(await candles.usdt_markets(market)) | {"BTCUSDT"})
            await candles.sync(market, syms, history_days=max(400, st.strategy.gate_sma + 60))
        close, qv = candles.panel()
        close = clean_glitches(close)
        d = last_closed_day()
        px = close.ffill().iloc[-1] if not close.empty else pd.Series(dtype=float)
        snap = MarketSnapshot(day=d, close=close, qvol=qv, prices=px.dropna().to_dict(), meta=meta,
                              shortable=meta.shortable(), age_hours=candles.data_age_hours(close))
        self.last_snapshot = snap
        return snap

    # ---------------- state ----------------
    @staticmethod
    def state() -> dict:
        return kv_get("live_state") or {"pending": [], "last_rebalance": {}, "peak": None, "last_marked": None,
                                        "last_run_day": None, "prev_prices": {}}

    @staticmethod
    def save_state(s: dict) -> None:
        kv_set("live_state", s)

    # ---------------- marking ----------------
    @staticmethod
    def mark(sleeves: list[Sleeve], prices: dict[str, float], prev: dict[str, float], days: int,
             meta: markets.MarketMeta, default_fee: float) -> dict:
        lp = sp = pf = 0.0
        for sl in sleeves:
            for s, q in sl.holdings.items():
                p0, p1 = prev.get(s), prices.get(s)
                if p0 and p1:
                    if q > 0:
                        lp += q * (p1 - p0)
                    else:
                        sp += q * (p1 - p0)
                if q < 0 and p1 and days > 0:
                    fee = meta.position_fee(s, default_fee) * (-q) * p1 * days
                    sl.cash -= fee
                    pf += fee
        return {"long_pnl": lp, "short_pnl": sp, "position_fees": pf}

    @staticmethod
    def valued(sleeves: list[Sleeve], prices: dict[str, float]) -> dict[str, float]:
        """Prices for every held symbol (falls back to entry price when a market has no price)."""
        out = dict(prices)
        for sl in sleeves:
            for s in sl.holdings:
                if s not in out or not out[s]:
                    out[s] = sl.entry.get(s, 0.0)
        return out

    # ---------------- daily run ----------------
    async def daily(self, manual: bool = False, force: bool = False) -> dict:
        async with self.lock:
            st = load_settings()
            market = self.market_client(st)
            try:
                return await self._daily(st, market, manual, force)
            except Exception as e:  # noqa: BLE001
                log.exception("daily run failed")
                add_alert("critical", "run_failed", f"Daily run failed: {e}")
                await self.notify(f"❌ Daily run failed: {e}", kind="alert")
                return {"status": "failed", "error": str(e)}
            finally:
                await market.close()

    async def _daily(self, st: Settings, market: NobitexClient, manual: bool, force: bool) -> dict:
        state = self.state()
        snap = await self.snapshot(st, market)
        d = snap.day
        if state.get("last_run_day") == str(d) and not force:
            return {"status": "already_ran", "day": str(d)}
        p = st.strategy

        # 1. stale data guard
        newest = snap.close.index.max().date() if not snap.close.empty else None
        if newest != d or snap.age_hours > st.risk.stale_data_hours:
            msg = f"Stale data: newest candle {newest}, expected {d} (age {snap.age_hours:.1f}h). Not trading."
            add_alert("critical", "stale_data", msg)
            await self.notify("⚠️ " + msg, kind="alert")
            run = self._record_run(st, d, "blocked", {"reason": msg})
            return {"status": "blocked", "run_id": run.id, "reason": msg}

        # 2. mark the live book
        sleeves = books.load_book(LIVE, p.tranches, st.capital_usdt)
        prices = self.valued(sleeves, snap.prices)
        last_marked = date.fromisoformat(state["last_marked"]) if state.get("last_marked") else None
        days = (d - last_marked).days if last_marked else 0
        parts = self.mark(sleeves, prices, state.get("prev_prices") or {}, max(days, 0), snap.meta,
                          st.costs.position_fee_daily)
        equity = sum(sl.equity(prices) for sl in sleeves)
        peak = max(state.get("peak") or equity, equity)
        dd = equity / peak - 1 if peak else 0.0
        gate = bool(features(snap.close, snap.qvol, p).gate.iloc[-1]) if p.gate_on else True
        state.update(last_marked=str(d), peak=peak, prev_prices={s: prices[s] for sl in sleeves for s in sl.holdings})
        books.save_book(LIVE, sleeves)
        lo = sum(sl.exposure(prices)[0] for sl in sleeves)
        sh = sum(sl.exposure(prices)[1] for sl in sleeves)
        books.record_equity(LIVE, d, equity=equity, gate=gate, gross=(lo + sh) / equity if equity else 0,
                            net=(lo - sh) / equity if equity else 0, **parts)

        # 3. shadow books (forward-only paper books for the 12 variants)
        if st.shadow.enabled:
            from .shadow.engine import update_shadows
            try:
                update_shadows(st, snap)
            except Exception:  # noqa: BLE001
                log.exception("shadow update failed")

        # 4. drawdown kill switch
        if dd <= -st.risk.kill_drawdown:
            self.save_state(state)
            await self.notify(f"🛑 Drawdown {dd:.1%} breached the kill limit {-st.risk.kill_drawdown:.0%}. "
                              "Flattening and pausing.", kind="alert")
            add_alert("critical", "kill_drawdown", f"Drawdown {dd:.1%}: kill switch")
            return await self._kill_locked(st, market, snap, reason=f"drawdown {dd:.1%}")

        if st.paused and not manual:
            state["last_run_day"] = str(d)
            self.save_state(state)
            run = self._record_run(st, d, "skipped", {"reason": "paused"})
            return {"status": "skipped", "run_id": run.id, "reason": "paused"}

        # 5. which sleeves trade today
        due = sorted({k for k in range(p.tranches) if (d.toordinal() - k) % p.rebalance_days == 0}
                     | set(state.get("pending") or []))
        f = features(snap.close, snap.qvol, p)
        t = targets_for(f, snap.shortable, pd.Timestamp(d), p)
        cap = st.risk.max_coin_frac_of_sleeve
        weights = {s: max(-cap, min(cap, w)) for s, w in t.weights.items()}
        desired: dict[int, dict[str, float]] = {}
        for k in due:
            sl = sleeves[k]
            e = sl.equity(prices)
            desired[k] = {s: w * e / prices[s] for s, w in weights.items() if prices.get(s)}

        # 6. liquidity filter on orders that open/increase exposure
        current = books.net_holdings(sleeves)
        target_net = self._net_after(sleeves, desired)
        illiquid = await self._liquidity_filter(market, current, target_net, prices, st)
        for k in desired:
            for s in illiquid:
                desired[k][s] = sleeves[k].holdings.get(s, 0.0)
        target_net = self._net_after(sleeves, desired)
        orders, dropped = plan_orders(current, target_net, prices, snap.meta, st.risk.min_order_usdt)

        detail = {
            "gate": t.gate, "due": due, "longs": t.longs, "shorts": t.shorts, "weights": weights,
            "ranked": t.ranked[:40], "eligible": t.eligible, "illiquid": illiquid, "dropped": dropped,
            "desired": {str(k): v for k, v in desired.items()}, "prices": {s: prices.get(s) for s in
                                                                           set(current) | set(target_net)},
            "equity": equity, "drawdown": dd, "params": p.model_dump(),
        }
        state["last_run_day"] = str(d)
        self.save_state(state)
        run = self._record_run(st, d, "started", detail, kind="manual" if manual else "rebalance")
        recs = self._record_orders(run.id, st.mode, orders)
        if not orders:
            await self._finish(run.id, st, sleeves, desired, {}, "executed")
            await self.notify(self._summary_text(st, run.id, detail, []), kind="run")
            return {"status": "executed", "run_id": run.id, "orders": 0, "detail": detail}

        if st.approval.enabled:
            appr = self._create_approval(run.id, st)
            self._set_run(run.id, "awaiting_approval")
            await self.notify(self._summary_text(st, run.id, detail, recs), kind="approval", approval_id=appr.id)
            return {"status": "awaiting_approval", "run_id": run.id, "approval_id": appr.id, "orders": len(recs)}

        return await self._execute_locked(run.id, st, market, snap.meta)

    @staticmethod
    def _net_after(sleeves: list[Sleeve], desired: dict[int, dict[str, float]]) -> dict[str, float]:
        out: dict[str, float] = {}
        for k, sl in enumerate(sleeves):
            h = desired.get(k, sl.holdings)
            for s, q in h.items():
                out[s] = out.get(s, 0.0) + q
        return {s: q for s, q in out.items() if abs(q) > 1e-12}

    async def _liquidity_filter(self, market: NobitexClient, current: dict, target: dict, prices: dict,
                                st: Settings) -> list[str]:
        bad = []
        for s in sorted(set(target)):
            old, new = current.get(s, 0.0), target.get(s, 0.0)
            grow = abs(new) > abs(old) or (old and new and (old > 0) != (new > 0))
            if not grow:
                continue
            notional = (abs(new) - (abs(old) if (old > 0) == (new > 0) else 0)) * prices[s]
            side = "buy" if new > 0 else "sell"
            try:
                book = await market.orderbook(s)
            except NobitexError:
                bad.append(s)
                continue
            bids, asks = book.get("bids") or [], book.get("asks") or []
            if not bids or not asks:
                bad.append(s)
                continue
            mid = (float(bids[0][0]) + float(asks[0][0])) / 2
            slip = slippage_of(book, side, notional, mid)
            if slip is None or slip > st.risk.max_slippage:
                bad.append(s)
        if bad:
            add_alert("warning", "illiquid", f"Skipped (order book too thin for {st.risk.max_slippage:.2%}): {', '.join(bad)}")
        return bad

    # ---------------- execution ----------------
    async def execute_run(self, run_id: int) -> dict:
        async with self.lock:
            st = load_settings()
            market = self.market_client(st)
            try:
                meta = await markets.refresh(market)
                return await self._execute_locked(run_id, st, market, meta)
            finally:
                await market.close()

    async def _execute_locked(self, run_id: int, st: Settings, market: NobitexClient, meta: markets.MarketMeta) -> dict:
        with session() as s:
            run = s.get(Run, run_id)
            recs = list(s.exec(select(OrderRec).where(OrderRec.run_id == run_id).order_by(OrderRec.id)))
        detail = run.detail or {}
        sleeves = books.load_book(LIVE, st.strategy.tranches, st.capital_usdt)
        desired = {int(k): v for k, v in (detail.get("desired") or {}).items()}
        broker, exch = self.broker(st, market, meta)
        try:
            if st.mode != "paper":
                issues = await broker.reconcile(books.net_holdings(sleeves))
                if issues:
                    msg = "Reconciliation mismatch, refusing to trade:\n" + "\n".join(issues[:15])
                    add_alert("critical", "reconcile", msg)
                    await self.notify("🛑 " + msg, kind="alert")
                    self._mark_orders(recs, "skipped")
                    self._set_run(run_id, "blocked", {"reconcile": issues})
                    self._add_pending(list(desired))
                    return {"status": "blocked", "issues": issues}
            todo = [r for r in recs if r.status in ("planned", "approved")]
            planned = [PlannedOrder(r.symbol, r.leg, r.side, r.action, r.amount, r.signal_price,
                                    full_close=r.action.startswith("close") and _is_full(r, sleeves)) for r in todo]
            if planned:
                await broker.prepare_cash(planned)
            fills: dict[str, list[tuple[float, float]]] = {}    # symbol -> [(signed qty, cash delta)]
            slip_alerts = []
            for r, po in zip(todo, planned):
                res = await broker.execute(po, r.client_id)
                signed = res.filled if po.side == "buy" else -res.filled
                if res.filled > 0:
                    fills.setdefault(po.symbol, []).append((signed, res.book_cash_delta(po.side)))
                    slip = ((res.avg_price / po.price - 1) if po.side == "buy" else (1 - res.avg_price / po.price))
                    if slip > st.costs.slippage * st.risk.slippage_alert_mult and slip > 0.002:
                        slip_alerts.append(f"{po.symbol} {po.side} slippage {slip:.2%} vs model {st.costs.slippage:.2%}")
                with session() as s:
                    o = s.get(OrderRec, r.id)
                    o.status, o.filled, o.avg_price, o.fee = res.status, res.filled, res.avg_price, res.fee
                    o.exchange_id, o.error = res.exchange_id, res.error
                    s.add(o)
                    s.commit()
            status = "executed" if all((x.status in ("filled", "rejected")) for x in self._orders(run_id)) else "partial"
            await self._finish(run_id, st, sleeves, desired, fills, status)
            if slip_alerts:
                add_alert("warning", "slippage", "; ".join(slip_alerts))
                await self.notify("⚠️ " + "\n".join(slip_alerts), kind="alert")
            await self.notify(self._exec_text(run_id), kind="run")
            return {"status": status, "run_id": run_id}
        finally:
            if exch:
                await exch.close()

    async def _finish(self, run_id: int, st: Settings, sleeves: list[Sleeve], desired: dict[int, dict[str, float]],
                      fills: dict[str, list[tuple[float, float]]], status: str) -> None:
        """Allocate actual net fills back to the sleeves that wanted them, pro rata."""
        changes: dict[str, dict[int, float]] = {}
        for k, h in desired.items():
            for s in set(h) | set(sleeves[k].holdings):
                dq = h.get(s, 0.0) - sleeves[k].holdings.get(s, 0.0)
                if abs(dq) > 1e-15:
                    changes.setdefault(s, {})[k] = dq
        for s, per in changes.items():
            want = sum(per.values())
            got = sum(q for q, _ in fills.get(s, []))
            cash = sum(c for _, c in fills.get(s, []))
            ratio = (got / want) if want else 0.0
            ratio = max(0.0, min(ratio, 1.0)) if want else 0.0
            px = abs(cash / got) if got else None
            for k, dq in per.items():
                sl = sleeves[k]
                share = dq / want if want else 0.0
                old = sl.holdings.get(s, 0.0)
                new = old + dq * ratio
                sl.cash += cash * share if want else 0.0
                if abs(new) < 1e-12:
                    sl.holdings.pop(s, None)
                    sl.entry.pop(s, None)
                else:
                    if px and (old == 0 or (old > 0) != (new > 0)):
                        sl.entry[s] = px
                    elif px and abs(new) > abs(old):
                        sl.entry[s] = (sl.entry.get(s, px) * abs(old) + px * (abs(new) - abs(old))) / abs(new)
                    sl.holdings[s] = new
        # symbols filled but not desired by any sleeve (should not happen): book to sleeve 0
        for s, fl in fills.items():
            if s not in changes and sleeves:
                sleeves[0].holdings[s] = sleeves[0].holdings.get(s, 0.0) + sum(q for q, _ in fl)
                sleeves[0].cash += sum(c for _, c in fl)
        state = self.state()
        pend = set(state.get("pending") or [])
        done = set(desired)
        if status in ("executed", "partial"):
            pend -= done
            lr = state.get("last_rebalance") or {}
            for k in done:
                lr[str(k)] = state.get("last_run_day")
            state["last_rebalance"] = lr
        else:
            pend |= done
        state["pending"] = sorted(pend)
        with session() as ss:
            run_prices = (ss.get(Run, run_id).detail or {}).get("prices") or {}
        prev = state.get("prev_prices") or {}
        for sl in sleeves:
            for s in sl.holdings:
                if s not in prev and run_prices.get(s):
                    prev[s] = run_prices[s]
        state["prev_prices"] = prev
        self.save_state(state)
        books.save_book(LIVE, sleeves, params=st.strategy.model_dump())
        self._set_run(run_id, status)

    # ---------------- approval ----------------
    def _create_approval(self, run_id: int, st: Settings) -> Approval:
        with session() as s:
            a = Approval(run_id=run_id, expires=utcnow() + timedelta(minutes=st.approval.timeout_minutes))
            s.add(a)
            s.commit()
            return a

    def decide(self, approval_id: int, approve: bool | None = None, order_id: int | None = None,
               keep_toggles: bool = False) -> Approval:
        """With order_id: set (or toggle when approve=None) one order. Without: decide the bundle; keep_toggles
        submits the per-order selection, otherwise every order gets `approve`."""
        with session() as s:
            a = s.get(Approval, approval_id)
            if a is None or a.status != "pending":
                return a
            recs = list(s.exec(select(OrderRec).where(OrderRec.run_id == a.run_id)))
            dec = dict(a.decisions or {})
            if order_id is not None:
                dec[str(order_id)] = not dec.get(str(order_id), True) if approve is None else approve
                a.decisions = dec
            else:
                for r in recs:
                    if not keep_toggles or str(r.id) not in dec:
                        dec[str(r.id)] = bool(approve)
                a.decisions = dec
                a.status = "approved" if any(dec.values()) else "rejected"
            s.add(a)
            s.commit()
            return a

    async def approval_tick(self) -> None:
        """Execute decided approvals, expire stale ones (expired = skip, retry next cycle)."""
        with session() as s:
            pend = list(s.exec(select(Approval).where(Approval.status.in_(["pending", "approved", "rejected"]))))
        for a in pend:
            with session() as s:
                run = s.get(Run, a.run_id)
            if run is None or run.status != "awaiting_approval":
                continue
            if a.status == "pending" and utcnow() >= a.expires:
                self._close_approval(a, "expired")
                await self.notify(f"⌛ Approval #{a.id} expired: run #{a.run_id} skipped, will retry next cycle.",
                                  kind="info")
                continue
            if a.status in ("approved", "rejected"):
                dec = a.decisions or {}
                with session() as s:
                    for r in s.exec(select(OrderRec).where(OrderRec.run_id == a.run_id)):
                        r.status = "approved" if dec.get(str(r.id), a.status == "approved") else "rejected"
                        s.add(r)
                    s.commit()
                if not any(dec.get(k) for k in dec) and a.status == "rejected":
                    self._close_approval(a, "rejected")
                    await self.notify(f"🚫 Run #{a.run_id} rejected. Will retry next cycle.", kind="info")
                    continue
                self._set_run(a.run_id, "started")
                await self.execute_run(a.run_id)

    def _close_approval(self, a: Approval, status: str) -> None:
        with session() as s:
            x = s.get(Approval, a.id)
            x.status = status
            s.add(x)
            for r in s.exec(select(OrderRec).where(OrderRec.run_id == a.run_id)):
                r.status = "skipped" if status == "expired" else "rejected"
                s.add(r)
            s.commit()
        with session() as s:
            run = s.get(Run, a.run_id)
            due = [int(k) for k in (run.detail or {}).get("desired", {})]
        self._set_run(a.run_id, "skipped", {"approval": status})
        self._add_pending(due)

    # ---------------- kill switch ----------------
    async def kill(self, reason: str = "manual") -> dict:
        async with self.lock:
            st = load_settings()
            market = self.market_client(st)
            try:
                snap = await self.snapshot(st, market, sync=False)
                return await self._kill_locked(st, market, snap, reason)
            finally:
                await market.close()

    async def _kill_locked(self, st: Settings, market: NobitexClient, snap: MarketSnapshot, reason: str) -> dict:
        st.paused = True
        save_settings(st)
        sleeves = books.load_book(LIVE, st.strategy.tranches, st.capital_usdt)
        prices = self.valued(sleeves, snap.prices)
        for s in books.net_holdings(sleeves):
            try:
                book = await market.orderbook(s)
                b, a = book.get("bids") or [], book.get("asks") or []
                if b and a:
                    prices[s] = (float(b[0][0]) + float(a[0][0])) / 2
            except NobitexError:
                pass
        desired = {k: {} for k in range(len(sleeves))}
        orders, dropped = plan_orders(books.net_holdings(sleeves), {}, prices, snap.meta, 0.0)
        run = self._record_run(st, snap.day, "started", {"reason": reason, "desired": {str(k): {} for k in desired},
                                                         "dropped": dropped}, kind="kill")
        self._record_orders(run.id, st.mode, orders)
        res = await self._execute_locked(run.id, st, market, snap.meta)
        await self.notify(f"🛑 Kill switch ({reason}): flattened, bot paused. Resume from the panel or /resume.",
                          kind="alert")
        return res

    # ---------------- records / text ----------------
    def _record_run(self, st: Settings, d: date, status: str, detail: dict, kind: str = "rebalance") -> Run:
        with session() as s:
            r = Run(day=d, mode=st.mode, kind=kind, status=status, detail=_jsonable(detail))
            s.add(r)
            s.commit()
            return r

    def _set_run(self, run_id: int, status: str, extra: dict | None = None) -> None:
        with session() as s:
            r = s.get(Run, run_id)
            r.status = status
            if extra:
                r.detail = (r.detail or {}) | _jsonable(extra)
            s.add(r)
            s.commit()

    def _add_pending(self, sleeves: list[int]) -> None:
        state = self.state()
        state["pending"] = sorted(set(state.get("pending") or []) | set(sleeves))
        self.save_state(state)

    def _record_orders(self, run_id: int, mode: str, orders: list[PlannedOrder]) -> list[OrderRec]:
        out = []
        with session() as s:
            for o in orders:
                r = OrderRec(run_id=run_id, mode=mode, client_id=new_client_id(), symbol=o.symbol, leg=o.leg,
                             side=o.side, action=o.action, amount=o.amount, signal_price=o.price)
                s.add(r)
                out.append(r)
            s.commit()
        return out

    @staticmethod
    def _orders(run_id: int) -> list[OrderRec]:
        with session() as s:
            return list(s.exec(select(OrderRec).where(OrderRec.run_id == run_id)))

    @staticmethod
    def _mark_orders(recs: list[OrderRec], status: str) -> None:
        with session() as s:
            for r in recs:
                x = s.get(OrderRec, r.id)
                if x.status in ("planned", "approved"):
                    x.status = status
                    s.add(x)
            s.commit()

    def _summary_text(self, st: Settings, run_id: int, detail: dict, recs: list[OrderRec]) -> str:
        lines = [f"📊 Run #{run_id} [{st.mode.upper()}] {st.strategy.label}",
                 f"Gate (BTC > SMA{st.strategy.gate_sma}): {'ON' if detail.get('gate') else 'OFF -> cash'}",
                 f"Sleeves due: {detail.get('due')}  Equity: {detail.get('equity', 0):.2f} USDT  "
                 f"DD: {detail.get('drawdown', 0):.1%}"]
        if detail.get("longs"):
            lines.append("Long: " + ", ".join(detail["longs"]))
        if detail.get("shorts"):
            lines.append("Short: " + ", ".join(detail["shorts"]))
        if detail.get("illiquid"):
            lines.append("Skipped (thin book): " + ", ".join(detail["illiquid"]))
        if recs:
            lines.append(f"Orders ({len(recs)}):")
            for r in recs:
                lines.append(f" #{r.id} {r.action} {r.symbol} {r.amount:g} ≈ {r.amount * r.signal_price:.2f} USDT")
        else:
            lines.append("No orders needed.")
        return "\n".join(lines)

    def _exec_text(self, run_id: int) -> str:
        recs = self._orders(run_id)
        lines = [f"✅ Run #{run_id} executed:"]
        for r in recs:
            px = f"@{r.avg_price:.6g}" if r.avg_price else ""
            lines.append(f" {r.status:8s} {r.action} {r.symbol} {r.filled:g}{px} fee {r.fee:.3f}")
        return "\n".join(lines)


def _is_full(r: OrderRec, sleeves: list[Sleeve]) -> bool:
    net = books.net_holdings(sleeves).get(r.symbol, 0.0)
    return abs(abs(net) - r.amount) <= max(1e-12, abs(net) * 0.002)


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (date, datetime, pd.Timestamp)):
        return str(x)
    if hasattr(x, "item"):
        return x.item()
    return x


ENGINE = Engine()
