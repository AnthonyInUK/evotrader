from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SignalRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_signals(self, rows: list[dict]) -> list[int]:
        if not rows:
            return []
        result = await self.db.execute(
            text(
                """
                INSERT INTO signals
                    (strategy_id, run_id, date, symbol, score, confidence, rationale)
                VALUES
                    (:strategy_id, :run_id, :date, :symbol, :score, :confidence, :rationale)
                RETURNING id
                """
            ),
            rows,
        )
        return [int(row[0]) for row in result]

    async def query_latest_by_strategy(
        self,
        strategy_id: str,
        signal_date: date | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict = {"strategy_id": strategy_id, "limit": limit}
        date_filter = ""
        if signal_date is not None:
            date_filter = "AND date = :signal_date"
            params["signal_date"] = signal_date
        result = await self.db.execute(
            text(
                f"""
                SELECT id, strategy_id, run_id, date, symbol, score,
                       confidence, rationale, created_at
                FROM signals
                WHERE strategy_id = :strategy_id {date_filter}
                ORDER BY date DESC, created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [dict(row._mapping) for row in result]
