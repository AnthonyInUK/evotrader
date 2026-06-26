from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_db
from db.repositories.backtest_repo import BacktestRepo
from db.repositories.observability_repo import DataQualityRepo, ObservabilityRepo

router = APIRouter(prefix="/api/v1/runs", tags=["observability"])


@router.get("")
async def list_runs(
    db: Annotated[AsyncSession, Depends(get_db)],
    strategy_id: str | None = None,
    limit: int = 50,
):
    return await BacktestRepo(db).list_runs(strategy_id=strategy_id, limit=limit)


@router.get("/{run_id}/trace")
async def run_trace(
    run_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    run = await BacktestRepo(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    traces = await ObservabilityRepo(db).query_stage_traces(run_id)
    return {**run, "stages": traces}


@router.get("/{run_id}/data-quality")
async def data_quality(
    run_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    run = await BacktestRepo(db).get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    reports = await DataQualityRepo(db).query_by_run_id(run_id)
    return {**run, "data_quality_reports": reports}
