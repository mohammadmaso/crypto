"""Daily report text and status payloads."""
from __future__ import annotations

from datetime import date, timedelta

from .. import books
from ..books import LIVE
from ..config import Settings
from ..shadow.engine import leaderboard


def status(st: Settings, engine) -> dict:
    pts = books.equity_series(LIVE)
    sleeves = books.load_book(LIVE, st.strategy.tranches, st.capital_usdt)
    state = engine.state()
    prices = state.get("prev_prices") or {}
    positions = []
    for k, sl in enumerate(sleeves):
        for s, q in sl.holdings.items():
            px = prices.get(s) or sl.entry.get(s, 0.0)
            positions.append({"sleeve": k, "symbol": s, "qty": q, "side": "long" if q > 0 else "short",
                              "entry": sl.entry.get(s), "price": px, "value": q * px,
                              "pnl": (px - sl.entry.get(s, px)) * q})
    eq = pts[-1].equity if pts else st.capital_usdt
    peak = state.get("peak") or eq
    return {
        "mode": st.mode, "paused": st.paused, "params": st.strategy.model_dump(), "label": st.strategy.label,
        "equity": eq, "capital": st.capital_usdt, "peak": peak, "drawdown": eq / peak - 1 if peak else 0,
        "gate": pts[-1].gate if pts else None, "last_run_day": state.get("last_run_day"),
        "pending_sleeves": state.get("pending") or [], "last_rebalance": state.get("last_rebalance") or {},
        "sleeves": [{"sleeve": k, "cash": sl.cash, "equity": sl.equity({s: prices.get(s) or sl.entry.get(s, 0.0)
                                                                         for s in sl.holdings})}
                    for k, sl in enumerate(sleeves)],
        "positions": sorted(positions, key=lambda p: (p["side"], -abs(p["value"]))),
        "equity_curve": [{"day": str(p.day), "equity": p.equity, "gate": p.gate, "long_pnl": p.long_pnl,
                          "short_pnl": p.short_pnl, "fees": p.fees, "position_fees": p.position_fees,
                          "gross": p.gross, "net": p.net} for p in pts],
    }


def daily_text(st: Settings, engine) -> str:
    s = status(st, engine)
    pts = books.equity_series(LIVE)
    cut = date.today() - timedelta(days=30)
    last30 = [p for p in pts if p.day >= cut]
    day_ret = (pts[-1].equity / pts[-2].equity - 1) if len(pts) >= 2 else 0.0
    lines = [
        f"📈 Daily report [{s['mode'].upper()}{' · PAUSED' if s['paused'] else ''}] {s['label']}",
        f"Equity {s['equity']:.2f} USDT ({day_ret:+.2%} day) · DD {s['drawdown']:.1%} · "
        f"Gate {'ON' if s['gate'] else 'OFF'}",
    ]
    if last30:
        lp = sum(p.long_pnl for p in last30)
        sp = sum(p.short_pnl for p in last30)
        fe = sum(p.fees for p in last30)
        pf = sum(p.position_fees for p in last30)
        lines.append(f"30d P&L: long {lp:+.2f} · short {sp:+.2f} · fees -{fe:.2f} · position fees -{pf:.2f}")
    longs = [p for p in s["positions"] if p["side"] == "long"]
    shorts = [p for p in s["positions"] if p["side"] == "short"]
    if longs:
        lines.append("Long: " + ", ".join(f"{p['symbol']}({p['value']:.0f})" for p in longs))
    if shorts:
        lines.append("Short: " + ", ".join(f"{p['symbol']}({-p['value']:.0f})" for p in shorts))
    if s["pending_sleeves"]:
        lines.append(f"Pending sleeves (retry): {s['pending_sleeves']}")
    return "\n".join(lines)


def leaderboard_text(st: Settings, top: int = 12) -> str:
    rows = leaderboard(st)
    lines = ["🧪 Shadow leaderboard (forward paper, Nobitex costs)"]
    for r in rows[:top]:
        mark = "★" if r["live"] else " "
        lines.append(f"{mark} {r['label']:<20} {r['days']:>4}d ret {r['return']:+.1%} Sh {r['sharpe']:.2f} DD {r['max_dd']:.1%}")
    return "\n".join(lines)
