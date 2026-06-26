from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class BacktestRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_run(
        self,
        strategy_id: str,
        start_date: date,
        end_date: date,
        status: str = "submitted",
    ) -> int:
        result = await self.db.execute(
            text(
                """
                INSERT INTO backtest_runs
                    (strategy_id, start_date, end_date, status)
                VALUES
                    (:strategy_id, :start_date, :end_date, :status)
                RETURNING id
                """
            ),
            {
                "strategy_id": strategy_id,
                "start_date": start_date,
                "end_date": end_date,
                "status": status,
            },
        )
        return int(result.scalar_one())

    async def update_status(self, run_id: int, status: str) -> None:
        await self.db.execute(
            text("UPDATE backtest_runs SET status = :status WHERE id = :run_id"),
            {"run_id": run_id, "status": status},
        )

    async def update_metrics(self, run_id: int, metrics: dict[str, Any]) -> None:
        await self.db.execute(
            text(
                """
                UPDATE backtest_runs
                SET metrics = CAST(:metrics AS jsonb)
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id, "metrics": json.dumps(metrics, ensure_ascii=False)},
        )

    async def get_run(self, run_id: int) -> dict | None:
        result = await self.db.execute(
            text(
                """
                SELECT id, strategy_id, start_date, end_date, status,
                       metrics, created_at
                FROM backtest_runs
                WHERE id = :run_id
                """
            ),
            {"run_id": run_id},
        )
        row = result.first()
        return dict(row._mapping) if row else None

    async def list_runs(
        self,
        strategy_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        where = ""
        if strategy_id:
            where = "WHERE strategy_id = :strategy_id"
            params["strategy_id"] = strategy_id
        result = await self.db.execute(
            text(
                f"""
                SELECT id, strategy_id, start_date, end_date, status,
                       metrics, created_at
                FROM backtest_runs
                {where}
                ORDER BY created_at DESC
                LIMIT :limit
                """
            ),
            params,
        )
        return [dict(row._mapping) for row in result]

    async def latest_metrics(self, strategy_id: str) -> dict | None:
        result = await self.db.execute(
            text(
                """
                SELECT metrics
                FROM backtest_runs
                WHERE strategy_id = :strategy_id AND status = 'completed'
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"strategy_id": strategy_id},
        )
        row = result.first()
        return dict(row._mapping["metrics"]) if row else None
