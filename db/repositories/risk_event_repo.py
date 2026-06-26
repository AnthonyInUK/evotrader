from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class RiskEventRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_events(self, rows: list[dict]) -> list[int]:
        if not rows:
            return []
        result = await self.db.execute(
            text(
                """
                INSERT INTO risk_events
                    (run_id, strategy_id, event_type, detail, triggered_at)
                VALUES
                    (:run_id, :strategy_id, :event_type, :detail, :triggered_at)
                RETURNING id
                """
            ),
            rows,
        )
        return [int(row[0]) for row in result]

    async def query(
        self,
        strategy_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict]:
        clauses = []
        params: dict = {}
        if strategy_id:
            clauses.append("strategy_id = :strategy_id")
            params["strategy_id"] = strategy_id
        if start_date:
            clauses.append("triggered_at::date >= :start_date")
            params["start_date"] = start_date
        if end_date:
            clauses.append("triggered_at::date <= :end_date")
            params["end_date"] = end_date
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        result = await self.db.execute(
            text(
                f"""
                SELECT id, run_id, strategy_id, event_type, detail, triggered_at
                FROM risk_events
                {where}
                ORDER BY triggered_at DESC
                """
            ),
            params,
        )
        return [dict(row._mapping) for row in result]
