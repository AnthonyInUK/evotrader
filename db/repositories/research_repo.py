from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ResearchRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_selection_attribution(self, rows: list[dict]) -> list[int]:
        if not rows:
            return []
        inserted: list[int] = []
        statement = text(
            """
            INSERT INTO selection_attribution
                (run_id, strategy_id, date, symbol, rank, bucket, score,
                 forward_return_1d, forward_return_5d, forward_return_10d,
                 benchmark_return_5d, excess_return_5d)
            VALUES
                (:run_id, :strategy_id, :date, :symbol, :rank, :bucket, :score,
                 :forward_return_1d, :forward_return_5d, :forward_return_10d,
                 :benchmark_return_5d, :excess_return_5d)
            RETURNING id
            """
        )
        for row in rows:
            result = await self.db.execute(statement, row)
            inserted.append(int(result.scalar_one()))
        return inserted

    async def insert_execution_checks(self, rows: list[dict]) -> list[int]:
        inserted: list[int] = []
        statement = text(
            """
            INSERT INTO execution_checks
                (run_id, strategy_id, date, symbol, action, approved,
                 adjusted_quantity, rejection_reason, warnings, transaction_cost,
                 capacity_ratio, slippage_estimate)
            VALUES
                (:run_id, :strategy_id, :date, :symbol, :action, :approved,
                 :adjusted_quantity, :rejection_reason, CAST(:warnings AS jsonb),
                 :transaction_cost, :capacity_ratio, :slippage_estimate)
            RETURNING id
            """
        )
        for row in rows:
            payload = {
                **row,
                "warnings": json.dumps(row.get("warnings") or [], ensure_ascii=False),
            }
            result = await self.db.execute(statement, payload)
            inserted.append(int(result.scalar_one()))
        return inserted

    async def insert_decision_audits(self, rows: list[dict]) -> list[int]:
        inserted: list[int] = []
        statement = text(
            """
            INSERT INTO decision_audits
                (run_id, strategy_id, date, symbol, action, audit_type,
                 severity, detail, evidence)
            VALUES
                (:run_id, :strategy_id, :date, :symbol, :action, :audit_type,
                 :severity, :detail, CAST(:evidence AS jsonb))
            RETURNING id
            """
        )
        for row in rows:
            payload = {
                **row,
                "evidence": json.dumps(
                    row.get("evidence") or {},
                    ensure_ascii=False,
                    default=str,
                ),
            }
            result = await self.db.execute(statement, payload)
            inserted.append(int(result.scalar_one()))
        return inserted

    async def query_selection_attribution(
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
                SELECT *
                FROM selection_attribution
                WHERE strategy_id = :strategy_id {date_filter}
                ORDER BY date DESC, rank ASC
                LIMIT :limit
                """
            ),
            params,
        )
        return [dict(row._mapping) for row in result]

    async def query_execution_checks(self, run_id: int) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT *
                FROM execution_checks
                WHERE run_id = :run_id
                ORDER BY symbol
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row._mapping) for row in result]

    async def query_decision_audits(self, run_id: int) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT *
                FROM decision_audits
                WHERE run_id = :run_id
                ORDER BY
                    CASE severity
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        ELSE 3
                    END,
                    symbol
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row._mapping) for row in result]
