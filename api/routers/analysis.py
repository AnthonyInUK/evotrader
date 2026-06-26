from __future__ import annotations

from datetime import date as Date
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import SessionLocal, get_db
from db.repositories.backtest_repo import BacktestRepo
from db.repositories.decision_repo import DecisionRepo
from pipeline.parallel_runner import run_all_strategies
from pipeline.runner import StrategyRunner
from risk.engine import RiskEngine

router = APIRouter(prefix="/api/v1/analysis", tags=["analysis"])


class RunRequest(BaseModel):
    strategy_id: str
    date: Date | None = None


async def _run_strategy_background(
    strategy_config,
    run_date: Date,
    run_id: int,
) -> None:
    async with SessionLocal() as session:
        runner = StrategyRunner(
            config=strategy_config,
            db=session,
            risk_engine=RiskEngine(),
        )
        await runner.run(run_date=run_date, run_id=run_id)


async def _run_all_background(run_date: Date, strategy_configs) -> None:
    await run_all_strategies(run_date, list(strategy_configs), db=None)


@router.post("/run")
async def run_analysis(
    payload: RunRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    strategy = request.app.state.strategy_map.get(payload.strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_id not found")
    run_date = payload.date or Date.today()
    repo = BacktestRepo(db)
    run_id = await repo.create_run(
        strategy_id=strategy.strategy_id,
        start_date=run_date,
        end_date=run_date,
        status="submitted",
    )
    await db.commit()
    background_tasks.add_task(_run_strategy_background, strategy, run_date, run_id)
    return {"run_id": run_id, "status": "submitted"}


@router.get("/{run_id}")
async def get_analysis(
    run_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    run = await BacktestRepo(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    decisions = await DecisionRepo(db).query_by_run_id(run_id)
    return {**run, "decisions": decisions}


@router.post("/{run_id}/retry")
async def retry_analysis(
    run_id: int,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = BacktestRepo(db)
    old_run = await repo.get_run(run_id)
    if old_run is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    strategy = request.app.state.strategy_map.get(old_run["strategy_id"])
    if strategy is None:
        raise HTTPException(status_code=404, detail="strategy_id not found")
    retry_id = await repo.create_run(
        strategy_id=strategy.strategy_id,
        start_date=old_run["start_date"],
        end_date=old_run["end_date"],
        status="submitted",
    )
    await db.commit()
    background_tasks.add_task(
        _run_strategy_background,
        strategy,
        old_run["start_date"],
        retry_id,
    )
    return {
        "source_run_id": run_id,
        "retry_run_id": retry_id,
        "status": "submitted",
    }


@router.post("/run-all")
async def run_all(
    background_tasks: BackgroundTasks,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    run_date: Date | None = None,
):
    target_date = run_date or Date.today()
    strategies = list(request.app.state.strategy_map.values())
    repo = BacktestRepo(db)
    submitted = []
    for strategy in strategies:
        run_id = await repo.create_run(
            strategy_id=strategy.strategy_id,
            start_date=target_date,
            end_date=target_date,
            status="submitted",
        )
        submitted.append({"strategy_id": strategy.strategy_id, "run_id": run_id})
    await db.commit()
    for item in submitted:
        strategy = request.app.state.strategy_map[item["strategy_id"]]
        background_tasks.add_task(
            _run_strategy_background,
            strategy,
            target_date,
            item["run_id"],
        )
    return submitted
