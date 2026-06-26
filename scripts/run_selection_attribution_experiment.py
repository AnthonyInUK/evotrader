#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.strategy_loader import load_strategy
from pipeline.universe_selection import run_universe_selection
from research.selection_attribution import (
    build_selection_attribution,
    summarize_selection_attribution,
)


def run_experiment(
    *,
    strategy_path: str,
    start_date: date,
    end_date: date,
    output_dir: Path,
) -> dict[str, Any]:
    config = load_strategy(strategy_path)
    daily: list[dict[str, Any]] = []
    selector_rows: list[dict[str, Any]] = []
    baseline_rows: list[dict[str, Any]] = []

    for run_date in _weekday_dates(start_date, end_date):
        selection = run_universe_selection(config, run_date)
        selector_attr = build_selection_attribution(
            run_id=0,
            strategy_id=config.strategy_id,
            run_date=run_date,
            selection_rows=selection.candidate_rows,
        )
        fixed_rows = [
            {
                "ticker": symbol,
                "bucket": "FIXED_BASELINE",
                "score": 0.0,
                "selection_reason": "Original YAML fixed universe baseline.",
            }
            for symbol in config.universe
        ]
        baseline_attr = build_selection_attribution(
            run_id=0,
            strategy_id=f"{config.strategy_id}_fixed_baseline",
            run_date=run_date,
            selection_rows=fixed_rows,
        )

        selector_rows.extend(selector_attr)
        baseline_rows.extend(baseline_attr)
        daily.append(
            {
                "date": run_date.isoformat(),
                "selected_symbols": selection.selected_symbols,
                "selection_summary": selection.summary,
                "selector": summarize_selection_attribution(selector_attr),
                "fixed_baseline": summarize_selection_attribution(baseline_attr),
            },
        )

    report = {
        "experiment": "selection_attribution",
        "strategy_id": config.strategy_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "trading_days": len(daily),
        "selector_aggregate": _aggregate(selector_rows),
        "fixed_baseline_aggregate": _aggregate(baseline_rows),
        "selector_by_bucket": _by_bucket(selector_rows),
        "daily": daily,
    }
    _write_outputs(report, selector_rows, baseline_rows, output_dir)
    return report


def _weekday_dates(start_date: date, end_date: date) -> list[date]:
    dates = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result = summarize_selection_attribution(rows)
    for horizon in [1, 5, 10]:
        values = [
            float(row[f"forward_return_{horizon}d"])
            for row in rows
            if row.get(f"forward_return_{horizon}d") is not None
        ]
        result[f"avg_forward_return_{horizon}d"] = _mean(values)
        result[f"win_rate_{horizon}d"] = _win_rate(values)
        result[f"coverage_{horizon}d"] = len(values)
    return result


def _by_bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("bucket") or "UNKNOWN")].append(row)
    return {
        bucket: _aggregate(bucket_rows)
        for bucket, bucket_rows in sorted(grouped.items())
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _win_rate(values: list[float]) -> float:
    return round(sum(1 for value in values if value > 0) / len(values), 6) if values else 0.0


def _write_outputs(
    report: dict[str, Any],
    selector_rows: list[dict[str, Any]],
    baseline_rows: list[dict[str, Any]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    key = f"{report['strategy_id']}_{report['start_date'].replace('-', '')}_{report['end_date'].replace('-', '')}"
    (output_dir / f"{key}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    _write_csv(output_dir / f"{key}_selector_rows.csv", selector_rows)
    _write_csv(output_dir / f"{key}_fixed_baseline_rows.csv", baseline_rows)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    columns = [
        "date",
        "symbol",
        "rank",
        "bucket",
        "score",
        "forward_return_1d",
        "forward_return_5d",
        "forward_return_10d",
        "benchmark_return_5d",
        "excess_return_5d",
    ]
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row.get(column, "")) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run universe selection attribution experiment.")
    parser.add_argument("--strategy", default="strategies/momentum_v1.yaml")
    parser.add_argument("--start-date", default="2024-02-20")
    parser.add_argument("--end-date", default="2024-02-26")
    parser.add_argument("--output-dir", default="outputs/experiments/selection_attribution")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_experiment(
        strategy_path=args.strategy,
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        output_dir=ROOT / args.output_dir,
    )
    print(
        json.dumps(
            {
                "trading_days": report["trading_days"],
                "selector_aggregate": report["selector_aggregate"],
                "fixed_baseline_aggregate": report["fixed_baseline_aggregate"],
                "selector_by_bucket": report["selector_by_bucket"],
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
