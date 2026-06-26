from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_db
from db.repositories.risk_event_repo import RiskEventRepo

router = APIRouter(prefix="/api/v1/risk-events", tags=["risk"])


@router.get("")
async def risk_events(
    db: Annotated[AsyncSession, Depends(get_db)],
    strategy_id: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    return await RiskEventRepo(db).query(
        strategy_id=strategy_id,
        start_date=start_date,
        end_date=end_date,
    )
