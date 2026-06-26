from __future__ import annotations

import asyncio
from datetime import date

from config.strategy_loader import StrategyConfig
from db.connection import SessionLocal
from pipeline.runner import RunResult, StrategyRunner
from risk.engine import RiskEngine


async def run_all_strategies(
    run_date: date,
    strategy_configs: list[StrategyConfig],
    db=None,
) -> list[RunResult]:
    async def run_one(config: StrategyConfig) -> RunResult:
        async with SessionLocal() as session:
            runner = StrategyRunner(
                config=config,
                db=session,
                risk_engine=RiskEngine(),
            )
            return await runner.run(run_date)

    return list(await asyncio.gather(*[run_one(config) for config in strategy_configs]))
