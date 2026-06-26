#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.connection import SessionLocal, close_db, init_db
from db.repositories.backtest_repo import BacktestRepo
from db.repositories.observability_repo import DataQualityRepo, ObservabilityRepo
from observability.tracing import trace_stage
from quality.data_quality import build_data_quality_report


async def main_async() -> dict:
    await init_db()
    async with SessionLocal() as db:
        backtests = BacktestRepo(db)
        run_id = await backtests.create_run(
            strategy_id="ops_smoke",
            start_date=date(2024, 2, 23),
            end_date=date(2024, 2, 23),
            status="running",
        )
        obs = ObservabilityRepo(db)
        dq_repo = DataQualityRepo(db)

        async with trace_stage(
            obs,
            run_id=run_id,
            strategy_id="ops_smoke",
            stage="market_data",
            metadata={"expected_symbols": 2},
        ) as trace:
            market = {
                "rows": [
                    {
                        "symbol": "600519.SH",
                        "date": date(2024, 2, 23),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10000,
                    },
                ],
                "prev_closes": {"600519.SH": 100.0},
            }
            report = build_data_quality_report(
                strategy_id="ops_smoke",
                run_date=date(2024, 2, 23),
                expected_symbols=["600519.SH", "000001.SZ"],
                market=market,
            )
            await dq_repo.insert_report(
                run_id=run_id,
                strategy_id="ops_smoke",
                report_date=date(2024, 2, 23),
                report=report,
                passed=bool(report["passed"]),
            )
            trace["quality_passed"] = report["passed"]
            trace["price_coverage"] = report["price_coverage"]

        await backtests.update_status(run_id, "completed")
        await db.commit()

        traces = await obs.query_stage_traces(run_id)
        reports = await dq_repo.query_by_run_id(run_id)
        runs = await backtests.list_runs(strategy_id="ops_smoke", limit=1)

    await close_db()
    return {
        "run_id": run_id,
        "trace_count": len(traces),
        "data_quality_count": len(reports),
        "data_quality_passed": reports[0]["passed"] if reports else None,
        "latest_status": runs[0]["status"] if runs else None,
        "missing_symbols": reports[0]["report"]["missing_symbols"] if reports else [],
    }


def main() -> int:
    print(asyncio.run(main_async()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
