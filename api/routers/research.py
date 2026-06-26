from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_db
from db.repositories.research_repo import ResearchRepo

router = APIRouter(prefix="/api/v1/research", tags=["research"])


@router.get("/selection-attribution")
async def selection_attribution(
    strategy_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    candidate_date: date | None = None,
    limit: int = 50,
):
    return await ResearchRepo(db).query_selection_attribution(
        strategy_id=strategy_id,
        candidate_date=candidate_date,
        limit=limit,
    )


@router.get("/execution-checks/{run_id}")
async def execution_checks(
    run_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await ResearchRepo(db).query_execution_checks(run_id)


@router.get("/decision-audits/{run_id}")
async def decision_audits(
    run_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    return await ResearchRepo(db).query_decision_audits(run_id)
