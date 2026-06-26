from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.jobs import daily_analysis_job

logger = logging.getLogger(__name__)


def start_scheduler(app) -> None:
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        daily_analysis_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=0,
            timezone="Asia/Shanghai",
        ),
        id="daily_analysis_job",
        replace_existing=True,
    )
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("EvoTraders scheduler started")


def stop_scheduler(app) -> None:
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("EvoTraders scheduler stopped")
