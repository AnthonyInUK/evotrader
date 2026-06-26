from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_db
from db.repositories.selection_repo import SelectionRepo

router = APIRouter(prefix="/api/v1/selection", tags=["selection"])


@router.get("/latest")
async def latest_selection(
    strategy_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    candidate_date: date | None = None,
    limit: int = 50,
):
    return await SelectionRepo(db).query_latest(
        strategy_id=strategy_id,
        candidate_date=candidate_date,
        limit=limit,
    )


@router.get("/run/{run_id}")
async def selection_by_run(
    run_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await SelectionRepo(db).query_by_run_id(run_id)
