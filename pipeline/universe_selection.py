from __future__ import annotations

from argparse import Namespace
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

from config.strategy_loader import StrategyConfig
from pipeline.market_regime import detect_market_regime


@dataclass
class UniverseSelectionResult:
    selected_symbols: list[str]
    candidate_rows: list[dict[str, Any]]
    summary: dict[str, Any]


def run_universe_selection(
    config: StrategyConfig,
    run_date: date,
) -> UniverseSelectionResult:
    if config.universe_mode != "selector" or config.selector is None:
        return _fixed_universe(config, reason="fixed_universe")

    try:
        selector = config.selector
        result = _screen_cached(
            run_date.isoformat(),
            selector.lookback_days,
            selector.theme_lookback_days,
            selector.top_sectors,
            selector.report_lag_days,
            selector.require_financial,
        )
        regime = (
            detect_market_regime(run_date, index_symbol=selector.regime_index)
            if selector.enable_regime_gating
            else None
        )
        allowed_buckets = _allowed_buckets_for_regime(config, regime.label if regime else None)
        rows = [
            row
            for row in result.get("rows", [])
            if str(row.get("bucket", "")).upper() in set(allowed_buckets)
        ]
        selected = rows[: selector.top_n]
        if not selected:
            fallback = _fixed_universe(config, reason="selector_empty_fallback")
            fallback.summary["selector_summary"] = _compact_summary(result)
            return fallback
        return UniverseSelectionResult(
            selected_symbols=[str(row["ticker"]) for row in selected],
            candidate_rows=selected,
            summary={
                "mode": "selector",
                "as_of": run_date.isoformat(),
                "selected_count": len(selected),
                "allowed_buckets": allowed_buckets,
                "regime_gating": {
                    "enabled": selector.enable_regime_gating,
                    "regime": regime.to_dict() if regime else None,
                },
                "selector_summary": _compact_summary(result),
            },
        )
    except Exception as exc:
        fallback = _fixed_universe(config, reason="selector_error_fallback")
        fallback.summary["error"] = f"{type(exc).__name__}: {exc}"
        return fallback


def selection_rows_for_db(
    *,
    run_id: int,
    strategy_id: str,
    run_date: date,
    selection: UniverseSelectionResult,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    selected = set(selection.selected_symbols)
    for rank, row in enumerate(selection.candidate_rows, start=1):
        symbol = str(row.get("ticker") or row.get("symbol") or "")
        bucket = str(row.get("bucket") or "UNKNOWN")
        rows.append(
            {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "date": run_date,
                "symbol": symbol,
                "rank": rank,
                "bucket": bucket,
                "score": _candidate_score(row, rank),
                "reason": str(row.get("selection_reason") or ""),
                "features": {
                    key: value
                    for key, value in row.items()
                    if key
                    not in {
                        "ticker",
                        "symbol",
                        "date",
                        "bucket",
                        "selection_reason",
                    }
                },
                "selected": symbol in selected,
            },
        )
    return rows


def _fixed_universe(config: StrategyConfig, reason: str) -> UniverseSelectionResult:
    rows = [
        {
            "ticker": symbol,
            "bucket": "FIXED_UNIVERSE",
            "selection_reason": "Use strategy YAML fixed universe.",
        }
        for symbol in config.universe
    ]
    return UniverseSelectionResult(
        selected_symbols=list(config.universe),
        candidate_rows=rows,
        summary={
            "mode": "fixed",
            "selected_count": len(config.universe),
            "fallback_reason": reason,
        },
    )


def _compact_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "as_of": result.get("as_of"),
        "price_cache_tickers": result.get("price_cache_tickers"),
        "price_insufficient": result.get("price_insufficient"),
        "analyzed": result.get("analyzed"),
        "theme_mapping_available": result.get("theme_mapping_available"),
        "counts": result.get("counts") or {},
        "hot_sectors": result.get("hot_sectors") or [],
    }


def _allowed_buckets_for_regime(
    config: StrategyConfig,
    regime_label: str | None,
) -> list[str]:
    selector = config.selector
    if selector is None or not selector.enable_regime_gating:
        return list(selector.allowed_buckets if selector else [])
    if regime_label == "weak":
        return selector.weak_allowed_buckets
    if regime_label == "sideways":
        return selector.sideways_allowed_buckets
    if regime_label == "rebound":
        return selector.rebound_allowed_buckets
    return selector.allowed_buckets


@lru_cache(maxsize=128)
def _screen_cached(
    as_of: str,
    lookback_days: int,
    theme_lookback_days: int,
    top_sectors: int,
    report_lag_days: int,
    require_financial: bool,
) -> dict[str, Any]:
    from scripts.screen_cached_universe import screen

    return screen(
        Namespace(
            as_of=as_of,
            lookback_days=lookback_days,
            theme_lookback_days=theme_lookback_days,
            top_sectors=top_sectors,
            report_lag_days=report_lag_days,
            require_financial=require_financial,
            max_rows=50,
            json_out=None,
            csv_out=None,
        ),
    )


def _candidate_score(row: dict[str, Any], rank: int) -> float:
    bucket_scores = {
        "CORE_CANDIDATE": 1.0,
        "ENTRY_SETUP_PULLBACK": 0.9,
        "TACTICAL_CANDIDATE": 0.78,
        "TECHNICAL_CANDIDATE": 0.68,
        "WATCHLIST": 0.35,
        "FIXED_UNIVERSE": 0.5,
    }
    bucket = str(row.get("bucket") or "").upper()
    momentum = float(row.get("ret60_pct") or 0.0) / 100.0
    score = bucket_scores.get(bucket, 0.2) + min(max(momentum, -0.2), 0.2)
    return round(max(score - rank * 0.001, 0.0), 4)
