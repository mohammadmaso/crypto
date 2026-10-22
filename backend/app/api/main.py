"""FastAPI app: REST API for the panel + static frontend + scheduler/Telegram lifecycle."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from .. import books
from ..books import LIVE
from ..config import ROOT, Settings, StrategyParams, base_url, secrets_for
from ..data import candles
from ..db import (Alert, Approval, OrderRec, Run, init_db, kv_set, latest_alerts, load_settings, save_settings,
                  session)
from ..engine import ENGINE
from ..exec.broker import NobitexBroker
from ..monitor.report import status as status_payload
from ..nobitex.client import NobitexClient, NobitexError
from ..scheduler import Scheduler
from ..shadow.engine import leaderboard, suggestion
from ..strategy.signal import clean_glitches, features, targets_for
from ..telegram.bot import TelegramService, test_proxy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("nobimomentum")

TG = TelegramService(ENGINE)
SCHED = Scheduler(ENGINE, TG)
LIVE_PHRASE = "I UNDERSTAND THIS TRADES REAL MONEY"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    save_settings(load_settings())
    ENGINE.notify = TG.notify
    await TG.restart()
    SCHED.start()
    yield
    SCHED.s.shutdown(wait=False)
    await TG.stop()


app = FastAPI(title="NobiMomentum", lifespan=lifespan)


# ---------------- status ----------------
@app.get("/api/status")
def get_status():
    st = load_settings()
    s = status_payload(st, ENGINE)
    s["telegram_running"] = TG.running
    s["keys_configured"] = secrets_for(st.mode).configured
    s["running"] = ENGINE.lock.locked()
    s["next_run"] = str(SCHED.s.get_job("daily").next_run_time) if SCHED.s.get_job("daily") else None
    return s


@app.get("/api/preview")
async def preview():
    """Today's signal from stored candles (no trading)."""
    st = load_settings()
    close, qv = candles.panel()
    if close.empty:
        raise HTTPException(409, "No candles yet. Run a sync or a rebalance first.")
    close = clean_glitches(close)
    f = features(close, qv, st.strategy)
    from ..data.markets import cached
    meta = cached()
    day = close.index.max()
    t = targets_for(f, meta.shortable(), day, st.strategy)
    btc = close["BTCUSDT"].ffill() if "BTCUSDT" in close else pd.Series(dtype=float)
    sma = btc.rolling(st.strategy.gate_sma).mean()
    return {"day": str(day.date()), "gate": t.gate, "btc": float(btc.iloc[-1]) if len(btc) else None,
            "btc_sma": float(sma.iloc[-1]) if len(sma) and sma.iloc[-1] == sma.iloc[-1] else None,
            "longs": t.longs, "shorts": t.shorts, "weights": t.weights,
            "ranked": [{"symbol": s, "mom": m, "shortable": s in meta.shortable()} for s, m in t.ranked],
            "data_age_hours": candles.data_age_hours(close)}


@app.post("/api/sync")
async def sync_data():
    st = load_settings()
    m = ENGINE.market_client(st)
    try:
        snap = await ENGINE.snapshot(st, m, sync=True)
        return {"day": str(snap.day), "symbols": len(snap.close.columns), "age_hours": snap.age_hours}
    finally:
        await m.close()


# ---------------- settings ----------------
def _masked(st: Settings) -> dict:
    d = st.model_dump()
    tok = d["telegram"]["bot_token"]
    d["telegram"]["bot_token"] = (tok[:6] + "…" + tok[-4:]) if tok else ""
    d["telegram"]["bot_token_set"] = bool(tok)
    return d


@app.get("/api/settings")
def get_settings():
    return _masked(load_settings())


@app.put("/api/settings")
async def put_settings(body: dict = Body(...)):
    cur = load_settings()
    data = cur.model_dump()
    for k in ("mode", "live_confirmed", "strategy"):
        body.pop(k, None)    # mode via /api/mode; strategy only via shadow apply or /api/strategy
    tg = body.get("telegram")
    if tg and ("…" in str(tg.get("bot_token", "")) or tg.get("bot_token") is None):
        tg["bot_token"] = cur.telegram.bot_token
    for k, v in body.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k] = data[k] | {kk: vv for kk, vv in v.items() if kk != "bot_token_set"}
        else:
            data[k] = v
    new = Settings.model_validate(data)
    save_settings(new)
    if new.telegram != cur.telegram:
        await TG.restart()
    if new.schedule != cur.schedule:
        SCHED.reschedule()
    return _masked(new)


@app.put("/api/strategy")
def put_strategy(body: dict = Body(...)):
    """Manual change of the frozen parameters (discouraged; STRATEGY.md section 7). Requires confirm=true."""
    if not body.pop("confirm", False):
        raise HTTPException(400, "confirm=true required")
    st = load_settings()
    st.strategy = StrategyParams.model_validate(st.strategy.model_dump() | body)
    save_settings(st)
    return st.strategy.model_dump()


@app.post("/api/mode")
async def set_mode(body: dict = Body(...)):
    mode = body.get("mode")
    st = load_settings()
    if mode not in ("paper", "testnet", "live"):
        raise HTTPException(400, "bad mode")
    if mode == st.mode:
        return {"mode": mode}
    if mode == "live" and body.get("confirm") != LIVE_PHRASE:
        raise HTTPException(400, f'Type exactly: "{LIVE_PHRASE}"')
    if mode != "paper":
        chk = await _test_nobitex(mode)
        if not chk["ok"]:
            raise HTTPException(400, f"Nobitex key check failed on {mode}: {chk.get('error')}")
    if books.net_holdings(books.load_book(LIVE, st.strategy.tranches, st.capital_usdt)) and not body.get("reset"):
        raise HTTPException(409, "The bot book has open positions. Flatten first (Kill) or pass reset=true to "
                                 "discard the book (only safe for paper).")
    books.reset_book(LIVE)
    kv_set("live_state", None)
    st.mode = mode
    st.live_confirmed = mode == "live"
    st.paused = mode != "paper"   # start paused on real exchanges; resume explicitly
    save_settings(st)
    return {"mode": mode, "paused": st.paused}


@app.post("/api/pause")
def pause():
    st = load_settings()
    st.paused = True
    save_settings(st)
    return {"paused": True}


@app.post("/api/resume")
def resume():
    st = load_settings()
    st.paused = False
    save_settings(st)
    return {"paused": False}


@app.post("/api/reset-book")
def reset_book(body: dict = Body(default={})):
    st = load_settings()
    if st.mode != "paper" and not body.get("force"):
        raise HTTPException(400, "Only the paper book can be reset (flatten real positions with Kill first).")
    books.reset_book(LIVE)
    kv_set("live_state", None)
    return {"ok": True}


# ---------------- connectivity tests ----------------
async def _test_nobitex(mode: str) -> dict:
    st = load_settings()
    sec = secrets_for(mode)
    if not sec.configured:
        return {"ok": False, "error": "NOBITEX_API_KEY / NOBITEX_API_SECRET missing in .env"}
    c = NobitexClient(base_url(mode), sec, proxy=st.nobitex_proxy_url or None)
    try:
        w = await c.wallets("spot")
        return {"ok": True, "wallets": len(w),
                "nonzero": {x["currency"]: x.get("balance") for x in w if float(x.get("balance") or 0) > 0}}
    except NobitexError as e:
        return {"ok": False, "error": str(e)}
    finally:
        await c.close()


@app.post("/api/test/nobitex")
async def test_nobitex(body: dict = Body(default={})):
    return await _test_nobitex(body.get("mode") or load_settings().mode if load_settings().mode != "paper" else "live")


@app.post("/api/test/telegram")
async def test_telegram(body: dict = Body(default={})):
    st = load_settings()
    proxy = body.get("proxy_url", st.telegram.proxy_url)
    token = body.get("bot_token") or st.telegram.bot_token
    if token and "…" in token:
        token = st.telegram.bot_token
    return await test_proxy(proxy, token or None)


@app.post("/api/test/telegram-message")
async def telegram_message():
    if not TG.running:
        raise HTTPException(409, "Telegram bot is not running (enable it and set token/chat id).")
    await TG.notify("👋 Test message from NobiMomentum.", kind="info")
    return {"ok": True}


@app.get("/api/account")
async def account():
    st = load_settings()
    if st.mode == "paper":
        return {"mode": "paper"}
    from ..data.markets import cached
    c = NobitexClient(base_url(st.mode), secrets_for(st.mode), proxy=st.nobitex_proxy_url or None)
    try:
        b = NobitexBroker(c, st, cached(), st.mode)
        return {"mode": st.mode, **(await b.account_summary()),
                "reconcile": await b.reconcile(books.net_holdings(books.load_book(LIVE, st.strategy.tranches,
                                                                                   st.capital_usdt)))}
    except NobitexError as e:
        raise HTTPException(502, str(e))
    finally:
        await c.close()


# ---------------- actions ----------------
@app.post("/api/run")
async def run_now(body: dict = Body(default={})):
    if ENGINE.lock.locked():
        raise HTTPException(409, "A run is already in progress")
    asyncio.create_task(ENGINE.daily(manual=True, force=bool(body.get("force", True))))
    return {"started": True}


@app.post("/api/kill")
async def kill(body: dict = Body(default={})):
    if body.get("confirm") is not True:
        raise HTTPException(400, "confirm=true required")
    return await ENGINE.kill("panel")


# ---------------- runs / orders / approvals / alerts ----------------
@app.get("/api/runs")
def runs(limit: int = 50):
    with session() as s:
        return [r.model_dump(exclude={"detail"}) | {"summary": _run_summary(r)}
                for r in s.exec(select(Run).order_by(Run.id.desc()).limit(limit))]


def _run_summary(r: Run) -> dict:
    d = r.detail or {}
    return {k: d.get(k) for k in ("gate", "due", "longs", "shorts", "illiquid", "reason", "equity")}


@app.get("/api/runs/{run_id}")
def run_detail(run_id: int):
    with session() as s:
        r = s.get(Run, run_id)
        if not r:
            raise HTTPException(404)
        orders = list(s.exec(select(OrderRec).where(OrderRec.run_id == run_id)))
        return {"run": r.model_dump(), "orders": [o.model_dump() for o in orders]}


@app.get("/api/orders")
def orders(limit: int = 200):
    with session() as s:
        return [o.model_dump() for o in s.exec(select(OrderRec).order_by(OrderRec.id.desc()).limit(limit))]


@app.get("/api/approvals")
def approvals(status: str | None = None):
    with session() as s:
        q = select(Approval).order_by(Approval.id.desc()).limit(50)
        if status:
            q = q.where(Approval.status == status)
        out = []
        for a in s.exec(q):
            recs = list(s.exec(select(OrderRec).where(OrderRec.run_id == a.run_id)))
            out.append(a.model_dump() | {"orders": [o.model_dump() for o in recs]})
        return out


@app.post("/api/approvals/{aid}/decide")
async def decide(aid: int, body: dict = Body(...)):
    a = ENGINE.decide(aid, body.get("approve"), order_id=body.get("order_id"),
                      keep_toggles=bool(body.get("keep_toggles")))
    if a is None:
        raise HTTPException(404)
    if a.status in ("approved", "rejected"):
        asyncio.create_task(ENGINE.approval_tick())
    return a.model_dump()


@app.get("/api/alerts")
def alerts(limit: int = 100):
    return [a.model_dump() for a in latest_alerts(limit)]


# ---------------- shadow / backtest ----------------
@app.get("/api/shadow")
def shadow():
    st = load_settings()
    return {"rows": leaderboard(st), "suggestion": suggestion(st), "min_days": st.shadow.min_days}


@app.post("/api/shadow/apply")
def shadow_apply(body: dict = Body(...)):
    """User-approved switch of the live parameters to a shadow variant."""
    st = load_settings()
    st.strategy = st.strategy.model_copy(update={k: body[k] for k in ("lookback", "k", "gate_on") if k in body})
    save_settings(st)
    from ..db import add_alert
    add_alert("info", "params_switched", f"Live parameters switched to {st.strategy.label} (user approved)")
    return st.strategy.model_dump()


@app.post("/api/backtest")
async def backtest(body: dict = Body(default={})):
    return await asyncio.to_thread(_backtest, body)


def _backtest(body: dict) -> dict:
    from ..backtest import binance_data
    from ..backtest.run import run_backtest, summarize
    from ..strategy.book import CostModel
    st = load_settings()
    p = StrategyParams.model_validate(st.strategy.model_dump() | (body.get("params") or {}))
    source = body.get("source", "binance")
    cost_kind = body.get("costs", "nobitex")
    costs = st.costs.model_copy(update=body.get("cost_overrides") or {})
    cost = CostModel.binance_research() if cost_kind == "binance" else CostModel.nobitex(costs)
    if source == "binance":
        if not binance_data.available():
            raise HTTPException(409, "Binance research data not found in ~/kronos_iran/crypto")
        close, qv, fund = binance_data.load()
        close = clean_glitches(close)
        res = run_backtest(close, qv, fund.notna(), p, cost, funding=fund if cost_kind == "binance" else None,
                           start=body.get("start", "2020-01-03"), end=body.get("end"))
    else:
        close, qv = candles.panel()
        if close.empty:
            raise HTTPException(409, "No Nobitex candles stored yet. Press Sync first.")
        from ..data.markets import cached
        close = clean_glitches(close)
        res = run_backtest(close, qv, cached().shortable(), p, cost, start=body.get("start"), end=body.get("end"))
    out = summarize(res)
    out["source"], out["costs"] = source, cost_kind
    btc = close["BTCUSDT"].reindex(res.returns.index).ffill()
    btc_eq = btc / btc.iloc[0]
    step = max(1, len(btc_eq) // 600)
    out["btc"] = [{"day": str(d.date()), "equity": float(v)} for d, v in btc_eq.iloc[::step].items()]
    return out


# ---------------- static frontend ----------------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        f = DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
