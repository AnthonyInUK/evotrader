from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config.env_config import get_env_float, get_env_int
from backend.core.pipeline import TradingPipeline
from backend.data.historical_price_manager import HistoricalPriceManager
from backend.main import create_agents
from backend.services.storage import StorageService
from backend.utils.settlement import SettlementCoordinator
from config.strategy_loader import StrategyConfig
from db.repositories.backtest_repo import BacktestRepo
from db.repositories.decision_repo import DecisionRepo
from db.repositories.market_data_repo import MarketDataRepo
from db.repositories.observability_repo import DataQualityRepo, ObservabilityRepo
from db.repositories.research_repo import ResearchRepo
from db.repositories.risk_event_repo import RiskEventRepo
from db.repositories.selection_repo import SelectionRepo
from db.repositories.signal_repo import SignalRepo
from execution.checks import build_execution_checks
from observability.tracing import trace_stage
from pipeline.universe_selection import run_universe_selection, selection_rows_for_db
from quality.data_quality import build_data_quality_report
from research.decision_audit import build_decision_audits
from research.selection_attribution import build_selection_attribution
from risk.engine import RiskEngine, RiskResult
from risk.metrics import calc_metrics_report


@dataclass
class RunResult:
    run_id: int
    strategy_id: str
    signals: list[dict]
    decisions: dict[str, Any]
    risk_events: list[RiskResult]
    metrics: dict[str, Any]
    selected_universe: list[str]
    selection_summary: dict[str, Any]


class StrategyRunner:
    def __init__(
        self,
        config: StrategyConfig,
        db: AsyncSession,
        risk_engine: RiskEngine,
    ):
        self.config = config
        self.db = db
        self.risk_engine = risk_engine
        self.market_data_repo = MarketDataRepo(db)
        self.signal_repo = SignalRepo(db)
        self.decision_repo = DecisionRepo(db)
        self.backtest_repo = BacktestRepo(db)
        self.risk_event_repo = RiskEventRepo(db)
        self.selection_repo = SelectionRepo(db)
        self.research_repo = ResearchRepo(db)
        self.observability_repo = ObservabilityRepo(db)
        self.data_quality_repo = DataQualityRepo(db)

    async def run(self, run_date: date, run_id: int | None = None) -> RunResult:
        if run_id is None:
            run_id = await self.backtest_repo.create_run(
                strategy_id=self.config.strategy_id,
                start_date=run_date,
                end_date=run_date,
                status="running",
            )
        else:
            await self.backtest_repo.update_status(run_id, "running")
        await self.db.commit()

        try:
            async with trace_stage(
                self.observability_repo,
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                stage="universe_selection",
                metadata={"mode": self.config.universe_mode, "run_date": run_date.isoformat()},
            ) as trace:
                selection = run_universe_selection(self.config, run_date)
                trace["selected_count"] = len(selection.selected_symbols)
                trace["summary"] = selection.summary
                self.config = self.config.model_copy(
                    update={"universe": selection.selected_symbols},
                )
                await self.selection_repo.insert_candidates(
                    selection_rows_for_db(
                        run_id=run_id,
                        strategy_id=self.config.strategy_id,
                        run_date=run_date,
                        selection=selection,
                    ),
                )
                await self.research_repo.insert_selection_attribution(
                    build_selection_attribution(
                        run_id=run_id,
                        strategy_id=self.config.strategy_id,
                        run_date=run_date,
                        selection_rows=selection.candidate_rows,
                    ),
                )

            async with trace_stage(
                self.observability_repo,
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                stage="market_data",
                metadata={"expected_symbols": len(self.config.universe)},
            ) as trace:
                market = await self._load_market_data(run_date)
                await self.market_data_repo.upsert_ohlcv(market["rows"])
                quality = build_data_quality_report(
                    strategy_id=self.config.strategy_id,
                    run_date=run_date,
                    expected_symbols=self.config.universe,
                    market=market,
                )
                await self.data_quality_repo.insert_report(
                    run_id=run_id,
                    strategy_id=self.config.strategy_id,
                    report_date=run_date,
                    report=quality,
                    passed=bool(quality["passed"]),
                )
                trace["loaded_symbols"] = len(market["rows"])
                trace["data_quality_passed"] = quality["passed"]
                trace["price_coverage"] = quality["price_coverage"]

            async with trace_stage(
                self.observability_repo,
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                stage="agent_analysis",
                metadata={"ticker_count": len(market["open_prices"])},
            ) as trace:
                previous_signals = await self.signal_repo.query_latest_by_strategy(
                    self.config.strategy_id,
                    limit=20,
                )
                pipeline_result = await self._run_existing_agent_pipeline(
                    run_date=run_date,
                    open_prices=market["open_prices"],
                    close_prices=market["close_prices"],
                    previous_signals=previous_signals,
                )
                trace["previous_signal_count"] = len(previous_signals)
                trace["analyst_result_count"] = len(pipeline_result.get("analyst_results", []))

            async with trace_stage(
                self.observability_repo,
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                stage="risk_execution_audit",
            ) as trace:
                signals = self._extract_signals(run_id, run_date, pipeline_result)
                risk_results = self.risk_engine.run_all_checks(
                    portfolio=pipeline_result.get("portfolio", {}),
                    config=self.config,
                    signals=signals,
                    current_drawdown=self._current_drawdown(pipeline_result),
                )
                sanitized_signals = self.risk_engine.sanitize_signals(
                    signals,
                    risk_results,
                )
                decision_rows = self._extract_decisions(run_id, run_date, pipeline_result)
                execution_checks = build_execution_checks(
                    run_id=run_id,
                    strategy_id=self.config.strategy_id,
                    run_date=run_date,
                    decisions=decision_rows,
                    market=market,
                )
                trace["signal_count"] = len(sanitized_signals)
                trace["decision_count"] = len(decision_rows)
                trace["risk_event_count"] = sum(1 for result in risk_results if not result.passed)
                trace["execution_blocked"] = sum(1 for item in execution_checks if not item["approved"])

            async with trace_stage(
                self.observability_repo,
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                stage="persistence_metrics",
            ) as trace:
                await self.signal_repo.insert_signals(sanitized_signals)
                await self.decision_repo.insert_decisions(decision_rows)
                await self.research_repo.insert_execution_checks(execution_checks)
                await self.research_repo.insert_decision_audits(
                    build_decision_audits(
                        run_id=run_id,
                        strategy_id=self.config.strategy_id,
                        run_date=run_date,
                        decisions=decision_rows,
                        signals=sanitized_signals,
                        execution_checks=execution_checks,
                    ),
                )
                await self.risk_event_repo.insert_events(
                    self._risk_rows(run_id, risk_results),
                )
                metrics = await calc_metrics_report(
                    run_id=run_id,
                    decisions=decision_rows,
                    backtest_repo=self.backtest_repo,
                    equity_curve=self._equity_curve(pipeline_result),
                    predicted_scores=[row["score"] for row in sanitized_signals],
                    actual_returns=[],
                )
                trace["metrics"] = metrics
            await self.backtest_repo.update_status(run_id, "completed")
            await self.db.commit()
            return RunResult(
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                signals=sanitized_signals,
                decisions=pipeline_result.get("pm_decisions", {}),
                risk_events=risk_results,
                metrics=metrics,
                selected_universe=selection.selected_symbols,
                selection_summary=selection.summary,
            )
        except Exception:
            failed_at = datetime.now().astimezone()
            await self.db.rollback()
            await self.observability_repo.insert_stage_trace(
                run_id=run_id,
                strategy_id=self.config.strategy_id,
                stage="pipeline",
                status="failed",
                started_at=failed_at,
                finished_at=failed_at,
                latency_ms=0.0,
                error_type="PipelineError",
                error_message="Pipeline failed; see application logs for full traceback.",
                metadata={"run_date": run_date.isoformat()},
            )
            await self.backtest_repo.update_status(run_id, "failed")
            await self.db.commit()
            raise

    async def _load_market_data(self, run_date: date) -> dict[str, Any]:
        run_date_s = run_date.isoformat()
        warmup_start = (
            run_date - timedelta(days=max(self.config.lookback_days, 1) + 150)
        ).isoformat()

        price_mgr = HistoricalPriceManager()
        price_mgr.subscribe(self.config.universe)
        price_mgr.preload_data(warmup_start, run_date_s)
        price_mgr.set_date(run_date_s)

        rows = []
        open_prices = {}
        close_prices = {}
        prev_closes = {}
        volumes = {}
        for symbol in self.config.universe:
            open_price = price_mgr.get_open_price(symbol)
            close_price = price_mgr.get_close_price(symbol)
            if open_price is None or close_price is None:
                continue
            df = price_mgr._price_cache.get(symbol)
            high = close_price
            low = close_price
            volume = 0
            if df is not None and not df.empty:
                day_rows = df[df["date"].astype(str) == run_date_s]
                if not day_rows.empty:
                    row = day_rows.iloc[-1]
                    high = float(row.get("high", close_price))
                    low = float(row.get("low", close_price))
                    volume = int(row.get("volume", 0) or 0)
                    prior_rows = df[df["date"].astype(str) < run_date_s]
                    if not prior_rows.empty:
                        prev_closes[symbol] = float(prior_rows.iloc[-1].get("close", close_price))
            rows.append(
                {
                    "symbol": symbol,
                    "date": run_date,
                    "open": float(open_price),
                    "high": float(high),
                    "low": float(low),
                    "close": float(close_price),
                    "volume": volume,
                    "source": "finance-mcp",
                }
            )
            open_prices[symbol] = float(open_price)
            close_prices[symbol] = float(close_price)
            volumes[symbol] = volume
            prev_closes.setdefault(symbol, float(close_price))
        return {
            "rows": rows,
            "open_prices": open_prices,
            "close_prices": close_prices,
            "prev_closes": prev_closes,
            "volumes": volumes,
        }

    async def _run_existing_agent_pipeline(
        self,
        run_date: date,
        open_prices: dict[str, float],
        close_prices: dict[str, float],
        previous_signals: list[dict],
    ) -> dict[str, Any]:
        initial_cash = get_env_float("INITIAL_CASH", 100000.0)
        margin_requirement = get_env_float("MARGIN_REQUIREMENT", 0.5)
        enable_memory = os.getenv("ENABLE_LONG_TERM_MEMORY", "").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        config_name = f"strategy_runs/{self.config.strategy_id}"
        Path(config_name).mkdir(parents=True, exist_ok=True)

        analysts, risk_manager, pm, memories = create_agents(
            config_name=config_name,
            initial_cash=initial_cash,
            margin_requirement=margin_requirement,
            enable_long_term_memory=enable_memory,
        )
        storage = StorageService(
            dashboard_dir=Path(config_name) / "team_dashboard",
            initial_cash=initial_cash,
            config_name=config_name,
        )
        pm.load_portfolio_state(storage.load_portfolio_state())
        settlement = SettlementCoordinator(storage=storage, initial_capital=initial_cash)

        async with contextlib.AsyncExitStack() as stack:
            for memory in memories:
                await stack.enter_async_context(memory)
            pipeline = TradingPipeline(
                analysts=analysts,
                risk_manager=risk_manager,
                portfolio_manager=pm,
                settlement_coordinator=settlement,
                max_comm_cycles=get_env_int("MAX_COMM_CYCLES", 1),
                config_name=config_name,
            )
            result = await pipeline.run_cycle(
                tickers=list(open_prices.keys()),
                date=run_date.isoformat(),
                prices=open_prices,
                close_prices=close_prices,
            )
            result["previous_signals"] = previous_signals
            return result

    def _extract_signals(
        self,
        run_id: int,
        run_date: date,
        result: dict[str, Any],
    ) -> list[dict]:
        signals = []
        for analyst_result in result.get("analyst_results", []):
            if not isinstance(analyst_result, dict):
                continue
            for symbol in self.config.universe:
                confidence = float(analyst_result.get("confidence", 50.0) or 50.0)
                signal = str(analyst_result.get("signal", "neutral")).lower()
                direction = 1.0 if "bull" in signal or "long" in signal else 0.0
                if "bear" in signal or "short" in signal:
                    direction = -1.0
                signals.append(
                    {
                        "strategy_id": self.config.strategy_id,
                        "run_id": run_id,
                        "date": run_date,
                        "symbol": symbol,
                        "score": direction * confidence / 100.0,
                        "confidence": confidence,
                        "rationale": str(analyst_result.get("reasoning", "")),
                    }
                )
        if signals:
            return signals
        return [
            {
                "strategy_id": self.config.strategy_id,
                "run_id": run_id,
                "date": run_date,
                "symbol": symbol,
                "score": 0.0,
                "confidence": 50.0,
                "rationale": "No structured analyst signal parsed.",
            }
            for symbol in self.config.universe
        ]

    def _extract_decisions(
        self,
        run_id: int,
        run_date: date,
        result: dict[str, Any],
    ) -> list[dict]:
        rows = []
        for symbol, decision in result.get("pm_decisions", {}).items():
            rows.append(
                {
                    "run_id": run_id,
                    "strategy_id": self.config.strategy_id,
                    "date": run_date,
                    "symbol": symbol,
                    "action": str(decision.get("action", "hold")),
                    "reasoning": str(decision.get("reasoning", "")),
                    "agent_votes": json.dumps(decision, ensure_ascii=False),
                }
            )
        return rows

    def _risk_rows(self, run_id: int, results: list[RiskResult]) -> list[dict]:
        return [
            {
                "run_id": run_id,
                "strategy_id": self.config.strategy_id,
                "event_type": result.event_type,
                "detail": result.detail,
                "triggered_at": result.triggered_at,
            }
            for result in results
            if not result.passed
        ]

    @staticmethod
    def _current_drawdown(result: dict[str, Any]) -> float:
        settlement = result.get("settlement_result") or {}
        return float(settlement.get("max_drawdown", 0.0) or 0.0)

    @staticmethod
    def _equity_curve(result: dict[str, Any]) -> list[float]:
        settlement = result.get("settlement_result") or {}
        curve = settlement.get("equity_curve") or settlement.get("nav_curve") or []
        return [float(item) for item in curve if item is not None]
