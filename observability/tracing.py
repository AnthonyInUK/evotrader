from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from db.repositories.observability_repo import ObservabilityRepo


@asynccontextmanager
async def trace_stage(
    repo: ObservabilityRepo,
    *,
    run_id: int,
    strategy_id: str,
    stage: str,
    metadata: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    started_at = datetime.now(timezone.utc)
    start_perf = time.perf_counter()
    stage_meta = dict(metadata or {})
    try:
        yield stage_meta
    except Exception as exc:
        finished_at = datetime.now(timezone.utc)
        await repo.insert_stage_trace(
            run_id=run_id,
            strategy_id=strategy_id,
            stage=stage,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(time.perf_counter() - start_perf) * 1000,
            error_type=type(exc).__name__,
            error_message=str(exc),
            metadata=stage_meta,
        )
        raise
    else:
        finished_at = datetime.now(timezone.utc)
        await repo.insert_stage_trace(
            run_id=run_id,
            strategy_id=strategy_id,
            stage=stage,
            status="success",
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=(time.perf_counter() - start_perf) * 1000,
            metadata=stage_meta,
        )
