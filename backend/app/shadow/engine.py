"""Shadow books: the 12 STRATEGY.md variants paper-traded on forward Nobitex data with the Nobitex cost model.

Live parameters never change on their own (STRATEGY.md section 7). A variant is *suggested* only when it beats the
live parameters over at least `min_days` of forward data by the configured margins; the user approves the switch.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from .. import books
from ..config import Settings, StrategyParams
from ..db import add_alert, kv_get, kv_set
from ..strategy.book import CostModel, rebalance, target_quantities
from ..strategy.signal import all_variants, features, targets_for

PREFIX = "shadow:"


def book_name(p: StrategyParams) -> str:
    return PREFIX + p.label


def update_shadows(st: Settings, snap) -> None:
    d: date = snap.day
    cost = CostModel.nobitex(st.costs)
    ts = pd.Timestamp(d)
    for v in all_variants():
        v = v.model_copy(update={"universe_size": st.strategy.universe_size, "rebalance_days": st.strategy.rebalance_days,
                                 "tranches": st.strategy.tranches})
        name = book_name(v)
        key = f"shadow_state:{name}"
        state = kv_get(key) or {"last_day": None, "prev": {}, "start": str(d)}
        if state["last_day"] == str(d):
            continue
        sleeves = books.load_book(name, v.tranches, st.capital_usdt)
        f = features(snap.close, snap.qvol, v)
        prices = dict(snap.prices)
        for sl in sleeves:
            for s in sl.holdings:
                prices.setdefault(s, sl.entry.get(s, 0.0))
        last = date.fromisoformat(state["last_day"]) if state["last_day"] else None
        days = (d - last).days if last else 0
        lp = sp = pf = fe = 0.0
        prev = state.get("prev") or {}
        for sl in sleeves:
            for s, q in sl.holdings.items():
                if prev.get(s) and prices.get(s):
                    if q > 0:
                        lp += q * (prices[s] - prev[s])
                    else:
                        sp += q * (prices[s] - prev[s])
                if q < 0 and days > 0:
                    fee = snap.meta.position_fee(s, st.costs.position_fee_daily) * (-q) * prices[s] * days
                    sl.cash -= fee
                    pf += fee
        t = targets_for(f, snap.shortable, ts, v) if ts in f.close.index else None
        for k, sl in enumerate(sleeves):
            if t is None or (d.toordinal() - k) % v.rebalance_days != 0:
                continue
            for fl in rebalance(sl, target_quantities(t.weights, sl.equity(prices), prices), prices, cost):
                fe += fl.fee
        eq = sum(sl.equity(prices) for sl in sleeves)
        lo = sum(sl.exposure(prices)[0] for sl in sleeves)
        sh = sum(sl.exposure(prices)[1] for sl in sleeves)
        books.save_book(name, sleeves, params=v.model_dump())
        books.record_equity(name, d, equity=eq, long_pnl=lp, short_pnl=sp, fees=fe, position_fees=pf,
                            gate=bool(t.gate) if t else None, gross=(lo + sh) / eq if eq else 0,
                            net=(lo - sh) / eq if eq else 0)
        state.update(last_day=str(d), prev={s: prices[s] for sl in sleeves for s in sl.holdings})
        kv_set(key, state)


def _stats(points) -> dict:
    if len(points) < 2:
        return {"days": len(points), "return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_dd": 0.0}
    eq = pd.Series([p.equity for p in points], index=[p.day for p in points])
    r = eq.pct_change().dropna()
    total = eq.iloc[-1] / eq.iloc[0] - 1
    years = max(len(r) / 365, 1 / 365)
    sd = r.std()
    return {
        "days": int(len(r)),
        "return": float(total),
        "cagr": float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0,
        "sharpe": float(r.mean() / sd * math.sqrt(365)) if sd and sd > 0 else 0.0,
        "max_dd": float((eq / eq.cummax() - 1).min()),
        "long_pnl": float(sum(p.long_pnl for p in points)),
        "short_pnl": float(sum(p.short_pnl for p in points)),
        "fees": float(sum(p.fees for p in points)),
        "position_fees": float(sum(p.position_fees for p in points)),
    }


def leaderboard(st: Settings) -> list[dict]:
    rows = []
    for v in all_variants():
        pts = books.equity_series(book_name(v))
        s = _stats(pts)
        rows.append({"label": v.label, "params": v.model_dump(include={"lookback", "k", "gate_on", "combo"}),
                     "live": v.combo == st.strategy.combo and (v.combo or v.lookback == st.strategy.lookback) and v.k == st.strategy.k and v.gate_on == st.strategy.gate_on,
                     "start": str(pts[0].day) if pts else None, **s,
                     "equity": [{"day": str(p.day), "equity": p.equity} for p in pts]})
    rows.sort(key=lambda r: -r["sharpe"])
    return rows


def suggestion(st: Settings) -> dict | None:
    rows = leaderboard(st)
    live = next((r for r in rows if r["live"]), None)
    if live is None or live["days"] < st.shadow.min_days:
        return None
    best = max((r for r in rows if not r["live"] and r["days"] >= st.shadow.min_days), key=lambda r: r["sharpe"],
               default=None)
    if best and best["sharpe"] - live["sharpe"] >= st.shadow.sharpe_margin and \
            best["return"] - live["return"] >= st.shadow.return_margin:
        return {"from": live["label"], "to": best["label"], "params": best["params"],
                "why": f"{best['days']}d forward: Sharpe {best['sharpe']:.2f} vs {live['sharpe']:.2f}, "
                       f"return {best['return']:.1%} vs {live['return']:.1%}"}
    return None


def apply_switch(params: dict) -> StrategyParams:
    """User-approved switch of the live parameters (Shadow page or Telegram button)."""
    from ..db import load_settings, save_settings
    st = load_settings()
    st.strategy = st.strategy.model_copy(update={k: params[k] for k in ("lookback", "k", "gate_on", "combo") if k in params})
    save_settings(st)
    add_alert("info", "params_switched", f"Live parameters switched to {st.strategy.label} (user approved)")
    return st.strategy


def short_leg_check(days: int = 90) -> dict | None:
    """Is the live short leg (price P&L minus position fees) net negative over the last `days`?"""
    pts = [p for p in books.equity_series(books.LIVE) if p.day >= date.today() - timedelta(days=days)]
    if len(pts) < days * 0.8:
        return None
    net = sum(p.short_pnl - p.position_fees for p in pts)
    if net < 0:
        return {"days": len(pts), "short_net": net,
                "why": f"Short leg lost {net:.2f} USDT over {len(pts)} days after position fees; "
                       "consider long-only (long_frac=1, short_frac=0)."}
    return None


def weekly_check(st: Settings) -> list[str]:
    msgs = []
    sug = suggestion(st)
    if sug and kv_get("last_suggestion") != sug["to"]:
        kv_set("last_suggestion", sug["to"])
        add_alert("info", "shadow_suggestion", f"Shadow suggests switching {sug['from']} -> {sug['to']}: {sug['why']}")
        msgs.append(f"🧪 Shadow suggestion: switch {sug['from']} → {sug['to']}\n{sug['why']}")
    sl = short_leg_check()
    if sl:
        msgs.append("🧪 " + sl["why"])
    return msgs
