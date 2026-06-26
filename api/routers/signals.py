from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.connection import get_db
from db.repositories.signal_repo import SignalRepo

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


@router.get("/latest")
async def latest_signals(
    strategy_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    signal_date: date | None = None,
    limit: int = 50,
):
    return await SignalRepo(db).query_latest_by_strategy(
        strategy_id=strategy_id,
        signal_date=signal_date,
        limit=limit,
    )
