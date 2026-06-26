from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_db
from db.repositories.backtest_repo import BacktestRepo

router = APIRouter(prefix="/api/v1/strategies", tags=["strategies"])


@router.get("")
async def list_strategies(request: Request):
    return [
        strategy.model_dump()
        for strategy in request.app.state.strategy_map.values()
    ]


@router.get("/{strategy_id}/metrics")
async def strategy_metrics(
    strategy_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    metrics = await BacktestRepo(db).latest_metrics(strategy_id)
    return {"strategy_id": strategy_id, "metrics": metrics or {}}


@router.get("/compare")
async def compare_strategies(
    strategy_ids: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    repo = BacktestRepo(db)
    ids = [item.strip() for item in strategy_ids.split(",") if item.strip()]
    return [
        {"strategy_id": strategy_id, "metrics": await repo.latest_metrics(strategy_id) or {}}
        for strategy_id in ids
    ]
