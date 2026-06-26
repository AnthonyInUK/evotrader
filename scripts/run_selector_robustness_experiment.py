#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
import sys
from argparse import Namespace
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.historical_price_manager import HistoricalPriceManager
from config.strategy_loader import load_strategy
from scripts.screen_cached_universe import screen


DEFAULT_WINDOWS = {
    "weak_202401": ("2024-01-02", "2024-01-31"),
    "rebound_202402": ("2024-02-20", "2024-03-18"),
    "sideways_202405": ("2024-05-06", "2024-05-31"),
}

ALLOWED_BUCKETS = {
    "CORE_CANDIDATE",
    "ENTRY_SETUP_PULLBACK",
    "TACTICAL_CANDIDATE",
    "TECHNICAL_CANDIDATE",
}


def run_experiment(
    *,
    strategy_path: str,
    windows: dict[str, tuple[str, str]],
    output_dir: Path,
    random_trials: int,
) -> dict[str, Any]:
    config = load_strategy(strategy_path)
    all_rows: list[dict[str, Any]] = []
    window_reports: dict[str, Any] = {}

    for window_name, (start_raw, end_raw) in windows.items():
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
        window_rows: list[dict[str, Any]] = []
        for run_date in _weekday_dates(start_date, end_date):
            screened = _screen_date(config, run_date)
            date_rows = _rows_for_date(
                config=config,
                run_date=run_date,
                screened=screened,
                random_trials=random_trials,
            )
            for row in date_rows:
                row["window"] = window_name
            window_rows.extend(date_rows)
            all_rows.extend(date_rows)
        window_reports[window_name] = {
            "start_date": start_raw,
            "end_date": end_raw,
            "trading_days": len(_weekday_dates(start_date, end_date)),
            "variants": _summarize_by_variant(window_rows),
        }

    report = {
        "experiment": "selector_robustness",
        "strategy_id": config.strategy_id,
        "random_trials": random_trials,
        "windows": window_reports,
        "overall": _summarize_by_variant(all_rows),
    }
    _write_outputs(report, all_rows, output_dir)
    return report


def _screen_date(config, run_date: date) -> dict[str, Any]:
    selector = config.selector
    if selector is None:
        raise ValueError("strategy selector config is required")
    return screen(
        Namespace(
            as_of=run_date.isoformat(),
            lookback_days=selector.lookback_days,
            theme_lookback_days=selector.theme_lookback_days,
            top_sectors=selector.top_sectors,
            report_lag_days=selector.report_lag_days,
            require_financial=selector.require_financial,
            max_rows=selector.top_n,
            json_out=None,
            csv_out=None,
        ),
    )


def _rows_for_date(
    *,
    config,
    run_date: date,
    screened: dict[str, Any],
    random_trials: int,
) -> list[dict[str, Any]]:
    rows = list(screened.get("rows") or [])
    allowed = [row for row in rows if str(row.get("bucket")) in ALLOWED_BUCKETS]
    fixed_baseline = [
        {"ticker": symbol, "bucket": "FIXED_BASELINE", "selection_reason": "YAML fixed universe"}
        for symbol in config.universe
    ]
    variants: dict[str, list[dict[str, Any]]] = {
        "all_top8": allowed[:8],
        "technical_only_top8": [row for row in allowed if row.get("bucket") == "TECHNICAL_CANDIDATE"][:8],
        "no_tactical_top8": [row for row in allowed if row.get("bucket") != "TACTICAL_CANDIDATE"][:8],
        "tactical_only_top8": [row for row in allowed if row.get("bucket") == "TACTICAL_CANDIDATE"][:8],
        "fixed_baseline": fixed_baseline,
    }
    for k in [3, 5, 8, 10, 15]:
        variants[f"top{k}"] = allowed[:k]

    rng = random.Random(int(run_date.strftime("%Y%m%d")))
    random_pool = [
        row for row in rows
        if row.get("exit_signal") == "NONE" and row.get("trend_state") != "NOT_RIGHT_SIDE"
    ]
    random_rows: list[dict[str, Any]] = []
    for trial in range(random_trials):
        if len(random_pool) < 8:
            sample = random_pool
        else:
            sample = rng.sample(random_pool, 8)
        random_rows.extend({**row, "_trial": trial} for row in sample)
    variants["random_cache_pool_top8"] = random_rows

    symbols = sorted(
        {
            str(row.get("ticker") or row.get("symbol"))
            for variant_rows in variants.values()
            for row in variant_rows
            if row.get("ticker") or row.get("symbol")
        },
    )
    returns = _load_forward_returns(symbols, run_date)

    out: list[dict[str, Any]] = []
    for variant, variant_rows in variants.items():
        for rank, row in enumerate(variant_rows, start=1):
            symbol = str(row.get("ticker") or row.get("symbol"))
            item = {
                "date": run_date.isoformat(),
                "variant": variant,
                "symbol": symbol,
                "rank": rank,
                "bucket": str(row.get("bucket") or "UNKNOWN"),
                "trial": row.get("_trial"),
                "forward_return_1d": returns.get(symbol, {}).get(1),
                "forward_return_5d": returns.get(symbol, {}).get(5),
                "forward_return_10d": returns.get(symbol, {}).get(10),
            }
            out.append(item)
    return out


def _load_forward_returns(symbols: list[str], run_date: date) -> dict[str, dict[int, float | None]]:
    price_mgr = HistoricalPriceManager()
    price_mgr.subscribe(symbols)
    price_mgr.preload_data(
        (run_date - timedelta(days=30)).isoformat(),
        (run_date + timedelta(days=25)).isoformat(),
    )
    return {
        symbol: {
            1: _forward_return(price_mgr, symbol, run_date, 1),
            5: _forward_return(price_mgr, symbol, run_date, 5),
            10: _forward_return(price_mgr, symbol, run_date, 10),
        }
        for symbol in symbols
    }


def _forward_return(
    price_mgr: HistoricalPriceManager,
    symbol: str,
    run_date: date,
    horizon: int,
) -> float | None:
    df = price_mgr._price_cache.get(symbol)
    if df is None or df.empty:
        return None
    frame = df.copy()
    if "date" in frame.columns:
        frame["_date"] = frame["date"].astype(str).str[:10]
    elif "time" in frame.columns:
        frame["_date"] = frame["time"].astype(str).str[:10]
    else:
        frame["_date"] = frame.index.astype(str).str[:10]
    frame = frame.sort_values("_date").reset_index(drop=True)
    matches = frame.index[frame["_date"] == run_date.isoformat()].tolist()
    if not matches:
        return None
    start_idx = matches[-1]
    end_idx = start_idx + horizon
    if end_idx >= len(frame):
        return None
    start = float(frame.loc[start_idx, "close"])
    end = float(frame.loc[end_idx, "close"])
    if start <= 0:
        return None
    return round(end / start - 1, 6)


def _summarize_by_variant(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["variant"]].append(row)
    return {
        variant: _aggregate(variant_rows)
        for variant, variant_rows in sorted(grouped.items())
    }


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {"count": len(rows)}
    for horizon in [1, 5, 10]:
        values = [
            float(row[f"forward_return_{horizon}d"])
            for row in rows
            if row.get(f"forward_return_{horizon}d") is not None
        ]
        summary[f"coverage_{horizon}d"] = len(values)
        summary[f"avg_forward_return_{horizon}d"] = _mean(values)
        summary[f"win_rate_{horizon}d"] = _win_rate(values)
    return summary


def _weekday_dates(start_date: date, end_date: date) -> list[date]:
    dates = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _win_rate(values: list[float]) -> float:
    return round(sum(1 for value in values if value > 0) / len(values), 6) if values else 0.0


def _write_outputs(report: dict[str, Any], rows: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    key = f"{report['strategy_id']}_selector_robustness"
    (output_dir / f"{key}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    columns = [
        "window",
        "date",
        "variant",
        "symbol",
        "rank",
        "bucket",
        "trial",
        "forward_return_1d",
        "forward_return_5d",
        "forward_return_10d",
    ]
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row.get(column, "")) for column in columns))
    (output_dir / f"{key}_rows.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run selector robustness experiments.")
    parser.add_argument("--strategy", default="strategies/momentum_v1.yaml")
    parser.add_argument("--output-dir", default="outputs/experiments/selector_robustness")
    parser.add_argument("--random-trials", type=int, default=20)
    parser.add_argument(
        "--windows",
        default=",".join(f"{name}:{start}:{end}" for name, (start, end) in DEFAULT_WINDOWS.items()),
        help="Comma-separated name:start:end windows.",
    )
    return parser.parse_args()


def _parse_windows(raw: str) -> dict[str, tuple[str, str]]:
    windows = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, start, end = item.split(":")
        windows[name] = (start, end)
    return windows


def main() -> int:
    args = parse_args()
    report = run_experiment(
        strategy_path=args.strategy,
        windows=_parse_windows(args.windows),
        output_dir=ROOT / args.output_dir,
        random_trials=args.random_trials,
    )
    compact = {
        "windows": {
            name: {
                "trading_days": item["trading_days"],
                "all_top8": item["variants"].get("all_top8"),
                "technical_only_top8": item["variants"].get("technical_only_top8"),
                "no_tactical_top8": item["variants"].get("no_tactical_top8"),
                "random_cache_pool_top8": item["variants"].get("random_cache_pool_top8"),
            }
            for name, item in report["windows"].items()
        },
        "overall": {
            key: report["overall"].get(key)
            for key in [
                "all_top8",
                "technical_only_top8",
                "no_tactical_top8",
                "tactical_only_top8",
                "top3",
                "top5",
                "top8",
                "top10",
                "top15",
                "fixed_baseline",
                "random_cache_pool_top8",
            ]
        },
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
