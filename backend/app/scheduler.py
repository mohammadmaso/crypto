"""Jobs: daily rebalance after the Tehran-midnight close, catch-up after sleep, approvals, reports."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .data.candles import day_open_ts, last_closed_day
from .db import load_settings

log = logging.getLogger(__name__)


def catchup_due(last_run_day: str | None, max_hours: float, now: float | None = None) -> bool:
    """True when the latest closed day has not run yet and we are still within the catch-up window."""
    now = time.time() if now is None else now
    d = last_closed_day(now)
    if last_run_day == str(d):
        return False
    closed_at = day_open_ts(d) + 86400
    return 0 <= (now - closed_at) / 3600 <= max_hours


class Scheduler:
    def __init__(self, engine, telegram) -> None:
        self.engine = engine
        self.tg = telegram
        self.s = AsyncIOScheduler(timezone=timezone.utc)

    def start(self) -> None:
        self.reschedule()
        self.s.add_job(self.engine.approval_tick, "interval", seconds=30, id="approvals", max_instances=1,
                       coalesce=True)
        self.s.add_job(self.catchup, "interval", minutes=5, id="catchup", max_instances=1, coalesce=True,
                       next_run_time=datetime.now(timezone.utc))
        self.s.start()

    def reschedule(self) -> None:
        st = load_settings().schedule
        self.s.add_job(self.daily, CronTrigger(hour=st.run_hour_utc, minute=st.run_minute_utc), id="daily",
                       replace_existing=True, misfire_grace_time=int(st.catchup_max_hours * 3600), coalesce=True)
        self.s.add_job(self.report, CronTrigger(hour=st.report_hour_utc, minute=st.report_minute_utc), id="report",
                       replace_existing=True, misfire_grace_time=6 * 3600, coalesce=True)
        self.s.add_job(self.weekly, CronTrigger(day_of_week="fri", hour=st.report_hour_utc, minute=10), id="weekly",
                       replace_existing=True, misfire_grace_time=24 * 3600, coalesce=True)

    async def daily(self) -> None:
        await self.engine.daily()

    async def catchup(self) -> None:
        """Covers a Mac that slept through the scheduled time: run once on wake if still inside the window."""
        st = load_settings()
        state = self.engine.state()
        if catchup_due(state.get("last_run_day"), st.schedule.catchup_max_hours) and not self.engine.lock.locked():
            log.info("catch-up run for %s", last_closed_day())
            await self.engine.daily()

    async def report(self) -> None:
        st = load_settings()
        if st.telegram.daily_report:
            from .monitor.report import daily_text
            await self.tg.notify(daily_text(st, self.engine), kind="report")

    async def weekly(self) -> None:
        st = load_settings()
        from .monitor.report import leaderboard_text
        from .shadow.engine import weekly_check
        if st.telegram.shadow_updates:
            await self.tg.notify(leaderboard_text(st), kind="shadow")
        for m in weekly_check(st):
            await self.tg.notify(m, kind="shadow")
