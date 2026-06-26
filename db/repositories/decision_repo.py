from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DecisionRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_decisions(self, rows: list[dict]) -> list[int]:
        if not rows:
            return []
        result = await self.db.execute(
            text(
                """
                INSERT INTO decisions
                    (run_id, strategy_id, date, symbol, action, reasoning, agent_votes)
                VALUES
                    (:run_id, :strategy_id, :date, :symbol, :action, :reasoning, CAST(:agent_votes AS jsonb))
                RETURNING id
                """
            ),
            rows,
        )
        return [int(row[0]) for row in result]

    async def query_by_run_id(self, run_id: int) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT id, run_id, strategy_id, date, symbol, action,
                       reasoning, agent_votes, created_at
                FROM decisions
                WHERE run_id = :run_id
                ORDER BY symbol
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row._mapping) for row in result]
