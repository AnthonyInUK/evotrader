from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class SelectionRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_candidates(self, rows: list[dict]) -> list[int]:
        if not rows:
            return []
        inserted: list[int] = []
        statement = text(
            """
            INSERT INTO selection_candidates
                (run_id, strategy_id, date, symbol, rank, bucket,
                 score, reason, features, selected)
            VALUES
                (:run_id, :strategy_id, :date, :symbol, :rank, :bucket,
                 :score, :reason, CAST(:features AS jsonb), :selected)
            RETURNING id
            """
        )
        for row in rows:
            payload = {
                **row,
                "features": json.dumps(
                    row.get("features") or {},
                    ensure_ascii=False,
                    default=str,
                ),
            }
            result = await self.db.execute(statement, payload)
            inserted.append(int(result.scalar_one()))
        return inserted

    async def query_by_run_id(self, run_id: int) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT id, run_id, strategy_id, date, symbol, rank, bucket,
                       score, reason, features, selected, created_at
                FROM selection_candidates
                WHERE run_id = :run_id
                ORDER BY rank ASC, score DESC
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row._mapping) for row in result]

    async def query_latest(
        self,
        strategy_id: str,
        candidate_date: date | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict = {"strategy_id": strategy_id, "limit": limit}
        date_filter = ""
        if candidate_date is not None:
            date_filter = "AND date = :candidate_date"
            params["candidate_date"] = candidate_date
        result = await self.db.execute(
            text(
                f"""
                SELECT id, run_id, strategy_id, date, symbol, rank, bucket,
                       score, reason, features, selected, created_at
                FROM selection_candidates
                WHERE strategy_id = :strategy_id {date_filter}
                ORDER BY date DESC, rank ASC, score DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [dict(row._mapping) for row in result]
