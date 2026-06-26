from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ObservabilityRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_stage_trace(
        self,
        *,
        run_id: int,
        strategy_id: str,
        stage: str,
        status: str,
        started_at: datetime,
        finished_at: datetime | None,
        latency_ms: float | None,
        retry_count: int = 0,
        error_type: str = "",
        error_message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> int:
        result = await self.db.execute(
            text(
                """
                INSERT INTO run_stage_traces
                    (run_id, strategy_id, stage, status, started_at, finished_at,
                     latency_ms, retry_count, error_type, error_message, metadata)
                VALUES
                    (:run_id, :strategy_id, :stage, :status, :started_at, :finished_at,
                     :latency_ms, :retry_count, :error_type, :error_message,
                     CAST(:metadata AS jsonb))
                RETURNING id
                """
            ),
            {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "stage": stage,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "latency_ms": latency_ms,
                "retry_count": retry_count,
                "error_type": error_type,
                "error_message": error_message[:1000],
                "metadata": json.dumps(metadata or {}, ensure_ascii=False, default=str),
            },
        )
        return int(result.scalar_one())

    async def query_stage_traces(self, run_id: int) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT id, run_id, strategy_id, stage, status, started_at, finished_at,
                       latency_ms, retry_count, error_type, error_message, metadata,
                       created_at
                FROM run_stage_traces
                WHERE run_id = :run_id
                ORDER BY started_at ASC, id ASC
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row._mapping) for row in result]


class DataQualityRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def insert_report(
        self,
        *,
        run_id: int,
        strategy_id: str,
        report_date,
        report: dict[str, Any],
        passed: bool,
    ) -> int:
        result = await self.db.execute(
            text(
                """
                INSERT INTO data_quality_reports
                    (run_id, strategy_id, date, report, passed)
                VALUES
                    (:run_id, :strategy_id, :date, CAST(:report AS jsonb), :passed)
                RETURNING id
                """
            ),
            {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "date": report_date,
                "report": json.dumps(report, ensure_ascii=False, default=str),
                "passed": passed,
            },
        )
        return int(result.scalar_one())

    async def query_by_run_id(self, run_id: int) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT id, run_id, strategy_id, date, report, passed, created_at
                FROM data_quality_reports
                WHERE run_id = :run_id
                ORDER BY created_at DESC
                """
            ),
            {"run_id": run_id},
        )
        return [dict(row._mapping) for row in result]
