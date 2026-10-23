"""Telegram bot through a local HTTP/SOCKS proxy: approvals, alerts, reports and commands.

Only the configured chat id may control the bot. Send /start from your Telegram account to see your chat id.
"""
from __future__ import annotations

import asyncio
import logging

import httpx
from sqlmodel import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

from ..db import Approval, OrderRec, load_settings, save_settings, session

log = logging.getLogger(__name__)


async def test_proxy(proxy_url: str, token: str | None = None) -> dict:
    """Checks the proxy reaches api.telegram.org (and the token, if given)."""
    url = f"https://api.telegram.org/bot{token}/getMe" if token else "https://api.telegram.org"
    try:
        async with httpx.AsyncClient(proxy=proxy_url or None, timeout=15) as c:
            r = await c.get(url)
        if token:
            d = r.json()
            return {"ok": bool(d.get("ok")), "bot": (d.get("result") or {}).get("username"),
                    "error": d.get("description")}
        return {"ok": r.status_code < 500, "status": r.status_code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


class TelegramService:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.app: Application | None = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self.app is not None

    async def restart(self) -> None:
        async with self._lock:
            await self._stop()
            st = load_settings().telegram
            if not (st.enabled and st.bot_token):
                return
            req = HTTPXRequest(proxy=st.proxy_url or None, connect_timeout=20, read_timeout=30)
            upd = HTTPXRequest(proxy=st.proxy_url or None, connect_timeout=20, read_timeout=60)
            app = Application.builder().token(st.bot_token).request(req).get_updates_request(upd).build()
            for name, fn in [("start", self.cmd_start), ("help", self.cmd_start), ("status", self.cmd_status),
                             ("positions", self.cmd_positions), ("pause", self.cmd_pause),
                             ("resume", self.cmd_resume), ("kill", self.cmd_kill), ("report", self.cmd_report),
                             ("leaderboard", self.cmd_leaderboard), ("run", self.cmd_run)]:
                app.add_handler(CommandHandler(name, fn))
            app.add_handler(CallbackQueryHandler(self.on_button))
            try:
                await app.initialize()
                await app.start()
                await app.updater.start_polling(drop_pending_updates=True)
                self.app = app
                log.info("telegram bot started")
            except Exception:  # noqa: BLE001
                log.exception("telegram start failed")

    async def _stop(self) -> None:
        if self.app is None:
            return
        try:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception:  # noqa: BLE001
            log.exception("telegram stop failed")
        self.app = None

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    # ---------- outbound ----------
    async def notify(self, text: str, kind: str = "info", approval_id: int | None = None,
                     switch: dict | None = None) -> None:
        st = load_settings().telegram
        if self.app is None or not st.chat_id:
            return
        if kind == "alert" and not st.alerts:
            return
        if kind == "shadow" and not st.shadow_updates:
            return
        markup = self._approval_markup(approval_id) if approval_id else None
        if switch:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Switch live parameters",
                                     callback_data=f"sw:{switch['lookback']}:{switch['k']}:{int(switch['gate_on'])}:{int(switch.get('combo', False))}"),
                InlineKeyboardButton("Ignore", callback_data="sw:no")]])
        if approval_id:
            text += f"\n\n⏳ Approve within {load_settings().approval.timeout_minutes} min, otherwise skipped."
        try:
            msg = await self.app.bot.send_message(chat_id=st.chat_id, text=text[:4000], reply_markup=markup)
            if approval_id:
                with session() as s:
                    a = s.get(Approval, approval_id)
                    a.telegram_message_id = msg.message_id
                    s.add(a)
                    s.commit()
        except Exception:  # noqa: BLE001
            log.exception("telegram send failed")

    def _approval_markup(self, approval_id: int) -> InlineKeyboardMarkup:
        with session() as s:
            a = s.get(Approval, approval_id)
            recs = list(s.exec(select(OrderRec).where(OrderRec.run_id == a.run_id).order_by(OrderRec.id)))
        dec = a.decisions or {}
        rows = [[InlineKeyboardButton("✅ Approve all", callback_data=f"ap:{approval_id}"),
                 InlineKeyboardButton("🚫 Reject all", callback_data=f"rj:{approval_id}")]]
        for r in recs[:40]:
            on = dec.get(str(r.id), True)
            rows.append([InlineKeyboardButton(f"{'✅' if on else '❌'} #{r.id} {r.action} {r.symbol}",
                                              callback_data=f"tg:{approval_id}:{r.id}")])
        if len(recs) > 1:
            rows.append([InlineKeyboardButton("▶️ Submit selection", callback_data=f"sb:{approval_id}")])
        return InlineKeyboardMarkup(rows)

    # ---------- inbound ----------
    def _allowed(self, update: Update) -> bool:
        chat = load_settings().telegram.chat_id
        return bool(chat) and update.effective_chat is not None and str(update.effective_chat.id) == str(chat)

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._allowed(update):
            await update.message.reply_text(
                f"Your chat id is {update.effective_chat.id}. Paste it into Settings → Telegram → Chat ID.")
            return
        await update.message.reply_text(
            "NobiMomentum bot\n/status /positions /report /leaderboard\n/pause /resume /kill /run")

    async def cmd_status(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        from ..monitor.report import daily_text
        await update.message.reply_text(daily_text(load_settings(), self.engine))

    cmd_report = cmd_status

    async def cmd_positions(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        from ..monitor.report import status
        s = status(load_settings(), self.engine)
        lines = [f"{p['side']:5s} {p['symbol']:<12} {p['qty']:+.6g} ≈ {p['value']:+.2f} USDT (sleeve {p['sleeve']})"
                 for p in s["positions"]] or ["No positions (all cash)."]
        await update.message.reply_text("\n".join(lines))

    async def cmd_leaderboard(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        from ..monitor.report import leaderboard_text
        await update.message.reply_text(leaderboard_text(load_settings()))

    async def cmd_pause(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        st = load_settings()
        st.paused = True
        save_settings(st)
        await update.message.reply_text("⏸ Paused. Positions are kept; no new rebalances.")

    async def cmd_resume(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        st = load_settings()
        st.paused = False
        save_settings(st)
        await update.message.reply_text("▶️ Resumed.")

    async def cmd_kill(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Yes, flatten everything", callback_data="kill:yes"),
                                    InlineKeyboardButton("Cancel", callback_data="kill:no")]])
        await update.message.reply_text("Close ALL positions and pause the bot?", reply_markup=kb)

    async def cmd_run(self, update: Update, ctx) -> None:
        if not self._allowed(update):
            return
        await update.message.reply_text("Running rebalance now…")
        asyncio.create_task(self.engine.daily(manual=True, force=True))

    async def on_button(self, update: Update, ctx) -> None:
        q = update.callback_query
        if not self._allowed(update):
            await q.answer("Not allowed")
            return
        parts = (q.data or "").split(":")
        await q.answer()
        if parts[0] == "kill":
            if parts[1] == "yes":
                await q.edit_message_text("🛑 Flattening…")
                asyncio.create_task(self.engine.kill("telegram"))
            else:
                await q.edit_message_text("Cancelled.")
            return
        if parts[0] == "sw":
            if parts[1] == "no":
                await q.edit_message_text((q.message.text or "") + "\n\nIgnored.")
            else:
                from ..shadow.engine import apply_switch
                p = apply_switch({"lookback": int(parts[1]), "k": int(parts[2]), "gate_on": parts[3] == "1",
                                    "combo": len(parts) > 4 and parts[4] == "1"})
                await q.edit_message_text((q.message.text or "") + f"\n\n✅ Live parameters now {p.label}")
            return
        aid = int(parts[1])
        if parts[0] == "tg":
            self.engine.decide(aid, None, order_id=int(parts[2]))
            await q.edit_message_reply_markup(self._approval_markup(aid))
            return
        approve = parts[0] in ("ap", "sb")
        a = self.engine.decide(aid, approve, keep_toggles=parts[0] == "sb")
        if a is None or a.status not in ("approved", "rejected"):
            await q.edit_message_text((q.message.text or "") + "\n\n(This approval is no longer pending.)")
            return
        await q.edit_message_text((q.message.text or "") + f"\n\n{'✅ Approved' if approve else '🚫 Rejected'}")
        asyncio.create_task(self.engine.approval_tick())
