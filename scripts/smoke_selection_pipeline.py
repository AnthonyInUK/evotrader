#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.strategy_loader import load_strategy
from db.connection import SessionLocal, close_db, init_db
from db.repositories.backtest_repo import BacktestRepo
from db.repositories.selection_repo import SelectionRepo
from pipeline.universe_selection import run_universe_selection, selection_rows_for_db


async def run_smoke(strategy_path: str, run_date: date) -> dict:
    await init_db()
    config = load_strategy(strategy_path)
    selection = run_universe_selection(config, run_date)

    async with SessionLocal() as db:
        backtests = BacktestRepo(db)
        run_id = await backtests.create_run(
            strategy_id=config.strategy_id,
            start_date=run_date,
            end_date=run_date,
            status="selection_smoke",
        )
        repo = SelectionRepo(db)
        inserted_ids = await repo.insert_candidates(
            selection_rows_for_db(
                run_id=run_id,
                strategy_id=config.strategy_id,
                run_date=run_date,
                selection=selection,
            ),
        )
        await db.commit()
        rows = await repo.query_by_run_id(run_id)

    await close_db()
    return {
        "run_id": run_id,
        "strategy_id": config.strategy_id,
        "date": run_date.isoformat(),
        "selection_mode": selection.summary.get("mode"),
        "selected_universe": selection.selected_symbols,
        "inserted": len(inserted_ids),
        "queried": len(rows),
        "first_candidate": rows[0]["symbol"] if rows else None,
        "first_bucket": rows[0]["bucket"] if rows else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test universe selection DB persistence.")
    parser.add_argument("--strategy", default="strategies/momentum_v1.yaml")
    parser.add_argument("--date", default="2024-02-23")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = asyncio.run(run_smoke(args.strategy, date.fromisoformat(args.date)))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
