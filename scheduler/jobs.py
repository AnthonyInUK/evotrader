from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from config.strategy_loader import load_all_strategies
from pipeline.parallel_runner import run_all_strategies

logger = logging.getLogger(__name__)


async def daily_analysis_job() -> None:
    """Runs every weekday at 16:00 CST after A-share market close."""
    strategy_dir = Path(__file__).resolve().parents[1] / "strategies"
    configs = load_all_strategies(strategy_dir)
    if not configs:
        logger.warning("No strategy configs found in %s", strategy_dir)
        return

    results = await run_all_strategies(date.today(), configs, db=None)
    risk_count = sum(len(result.risk_events) for result in results)
    logger.info(
        "Daily analysis complete: %d strategies run, %d risk events triggered",
        len(results),
        risk_count,
    )
    for result in results:
        logger.info(
            "strategy=%s run_id=%s signals=%d metrics=%s",
            result.strategy_id,
            result.run_id,
            len(result.signals),
            result.metrics,
        )
