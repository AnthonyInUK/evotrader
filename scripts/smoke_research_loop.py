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
from db.repositories.research_repo import ResearchRepo
from db.repositories.selection_repo import SelectionRepo
from execution.checks import build_execution_checks
from pipeline.universe_selection import run_universe_selection, selection_rows_for_db
from research.decision_audit import build_decision_audits
from research.selection_attribution import (
    build_selection_attribution,
    summarize_selection_attribution,
)


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
            status="research_smoke",
        )

        selection_repo = SelectionRepo(db)
        selection_db_rows = selection_rows_for_db(
            run_id=run_id,
            strategy_id=config.strategy_id,
            run_date=run_date,
            selection=selection,
        )
        await selection_repo.insert_candidates(selection_db_rows)

        research_repo = ResearchRepo(db)
        attribution_rows = build_selection_attribution(
            run_id=run_id,
            strategy_id=config.strategy_id,
            run_date=run_date,
            selection_rows=selection.candidate_rows,
        )
        await research_repo.insert_selection_attribution(attribution_rows)

        decisions = _demo_decisions(run_id, config.strategy_id, run_date, selection.selected_symbols)
        signals = _demo_signals(run_id, config.strategy_id, run_date, selection.selected_symbols)
        execution_checks = build_execution_checks(
            run_id=run_id,
            strategy_id=config.strategy_id,
            run_date=run_date,
            decisions=decisions,
            market=_demo_market(selection.selected_symbols),
        )
        await research_repo.insert_execution_checks(execution_checks)
        audit_rows = build_decision_audits(
            run_id=run_id,
            strategy_id=config.strategy_id,
            run_date=run_date,
            decisions=decisions,
            signals=signals,
            execution_checks=execution_checks,
        )
        await research_repo.insert_decision_audits(audit_rows)
        await db.commit()

        queried_checks = await research_repo.query_execution_checks(run_id)
        queried_audits = await research_repo.query_decision_audits(run_id)

    await close_db()
    return {
        "run_id": run_id,
        "selected": selection.selected_symbols,
        "selection_attribution": summarize_selection_attribution(attribution_rows),
        "execution_checks": len(queried_checks),
        "decision_audits": len(queried_audits),
        "first_audit": queried_audits[0]["audit_type"] if queried_audits else None,
    }


def _demo_decisions(
    run_id: int,
    strategy_id: str,
    run_date: date,
    symbols: list[str],
) -> list[dict]:
    rows = []
    for index, symbol in enumerate(symbols[:4]):
        rows.append(
            {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "date": run_date,
                "symbol": symbol,
                "action": "buy" if index < 2 else "hold",
                "reasoning": "Smoke decision for research-loop validation.",
                "agent_votes": {"quantity": 150 if index == 0 else 100},
            },
        )
    return rows


def _demo_signals(
    run_id: int,
    strategy_id: str,
    run_date: date,
    symbols: list[str],
) -> list[dict]:
    rows = []
    for symbol in symbols[:4]:
        rows.extend(
            [
                {
                    "run_id": run_id,
                    "strategy_id": strategy_id,
                    "date": run_date,
                    "symbol": symbol,
                    "score": 0.7,
                    "confidence": 82,
                },
                {
                    "run_id": run_id,
                    "strategy_id": strategy_id,
                    "date": run_date,
                    "symbol": symbol,
                    "score": -0.4,
                    "confidence": 78,
                },
            ],
        )
    return rows


def _demo_market(symbols: list[str]) -> dict:
    return {
        "open_prices": {symbol: 10.0 for symbol in symbols},
        "prev_closes": {symbol: 9.9 for symbol in symbols},
        "volumes": {symbol: 10_000 for symbol in symbols},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test research attribution/check/audit loop.")
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
