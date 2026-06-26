#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
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


DEFAULT_WINDOWS = {
    "weak_202401": ("2024-01-02", "2024-01-15"),
    "rebound_202402": ("2024-02-20", "2024-03-04"),
    "sideways_202405": ("2024-05-06", "2024-05-17"),
}


def run_experiment(
    *,
    strategy_path: str,
    windows: dict[str, tuple[str, str]],
    output_dir: Path,
) -> dict[str, Any]:
    base_config = load_strategy(strategy_path)
    if base_config.selector is None:
        raise ValueError("strategy selector config is required")

    ungated_config = base_config.model_copy(
        update={
            "selector": base_config.selector.model_copy(
                update={"enable_regime_gating": False},
            ),
        },
    )
    gated_config = base_config.model_copy(
        update={
            "selector": base_config.selector.model_copy(
                update={"enable_regime_gating": True},
            ),
        },
    )

    all_rows: list[dict[str, Any]] = []
    window_reports = {}
    for window_name, (start_raw, end_raw) in windows.items():
        start_date = date.fromisoformat(start_raw)
        end_date = date.fromisoformat(end_raw)
        rows = []
        for run_date in _weekday_dates(start_date, end_date):
            rows.extend(_run_one_day("ungated", ungated_config, run_date))
            rows.extend(_run_one_day("gated", gated_config, run_date))
        all_rows.extend({**row, "window": window_name} for row in rows)
        window_reports[window_name] = {
            "start_date": start_raw,
            "end_date": end_raw,
            "trading_days": len(_weekday_dates(start_date, end_date)),
            "variants": _summarize_by_variant(rows),
            "delta": _delta(_summarize_by_variant(rows)),
        }

    report = {
        "experiment": "regime_gating_ab",
        "strategy_id": base_config.strategy_id,
        "windows": window_reports,
        "overall": {
            "variants": _summarize_by_variant(all_rows),
            "delta": _delta(_summarize_by_variant(all_rows)),
        },
    }
    _write_outputs(report, all_rows, output_dir)
    return report


def _run_one_day(variant: str, config, run_date: date) -> list[dict[str, Any]]:
    selection = run_universe_selection(config, run_date)
    attr = build_selection_attribution(
        run_id=0,
        strategy_id=f"{config.strategy_id}_{variant}",
        run_date=run_date,
        selection_rows=selection.candidate_rows,
    )
    rows = []
    for row in attr:
        rows.append(
            {
                "date": run_date.isoformat(),
                "variant": variant,
                "symbol": row["symbol"],
                "rank": row["rank"],
                "bucket": row["bucket"],
                "forward_return_1d": row["forward_return_1d"],
                "forward_return_5d": row["forward_return_5d"],
                "forward_return_10d": row["forward_return_10d"],
                "allowed_buckets": selection.summary.get("allowed_buckets"),
                "regime": (
                    (selection.summary.get("regime_gating") or {})
                    .get("regime") or {}
                ).get("label"),
            },
        )
    return rows


def _summarize_by_variant(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = {"ungated": [], "gated": []}
    for row in rows:
        grouped.setdefault(row["variant"], []).append(row)
    return {variant: _aggregate(items) for variant, items in grouped.items()}


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


def _delta(summary: dict[str, Any]) -> dict[str, Any]:
    gated = summary.get("gated") or {}
    ungated = summary.get("ungated") or {}
    return {
        "count_delta": int(gated.get("count", 0)) - int(ungated.get("count", 0)),
        "t1_avg_return_delta": round(
            float(gated.get("avg_forward_return_1d", 0.0))
            - float(ungated.get("avg_forward_return_1d", 0.0)),
            6,
        ),
        "t5_avg_return_delta": round(
            float(gated.get("avg_forward_return_5d", 0.0))
            - float(ungated.get("avg_forward_return_5d", 0.0)),
            6,
        ),
        "t10_avg_return_delta": round(
            float(gated.get("avg_forward_return_10d", 0.0))
            - float(ungated.get("avg_forward_return_10d", 0.0)),
            6,
        ),
        "t5_win_rate_delta": round(
            float(gated.get("win_rate_5d", 0.0))
            - float(ungated.get("win_rate_5d", 0.0)),
            6,
        ),
    }


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
    key = f"{report['strategy_id']}_regime_gating_ab"
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
        "regime",
        "allowed_buckets",
        "forward_return_1d",
        "forward_return_5d",
        "forward_return_10d",
    ]
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row.get(column, "")).replace(",", ";") for column in columns))
    (output_dir / f"{key}_rows.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_windows(raw: str) -> dict[str, tuple[str, str]]:
    windows = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, start, end = item.split(":")
        windows[name] = (start, end)
    return windows


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run regime gating A/B experiment.")
    parser.add_argument("--strategy", default="strategies/momentum_v1.yaml")
    parser.add_argument("--output-dir", default="outputs/experiments/regime_gating_ab")
    parser.add_argument(
        "--windows",
        default=",".join(f"{name}:{start}:{end}" for name, (start, end) in DEFAULT_WINDOWS.items()),
        help="Comma-separated name:start:end windows.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_experiment(
        strategy_path=args.strategy,
        windows=_parse_windows(args.windows),
        output_dir=ROOT / args.output_dir,
    )
    compact = {
        "windows": {
            name: {
                "trading_days": item["trading_days"],
                "variants": item["variants"],
                "delta": item["delta"],
            }
            for name, item in report["windows"].items()
        },
        "overall": report["overall"],
    }
    print(json.dumps(compact, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
