# -*- coding: utf-8 -*-
"""
Week 3 milestone acceptance for EvoTraders.

What it does:
1. Backfill stats.json from summary.json using the Day 19-20 quant metrics schema.
2. Normalize leaderboard.json into the Day 17-18 multidimensional schema.
3. Generate a standalone HTML backtest report.
4. Emit a machine-readable milestone report for Day 21 acceptance.
"""
import argparse
import json
import math
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from typing import Any, Dict, List, Optional

TRADING_DAYS_PER_YEAR = 252


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _safe_diff(
    first: Optional[float],
    second: Optional[float],
) -> Optional[float]:
    if first is None or second is None:
        return None
    return round(first - second, 3)


def _round_optional_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value * 100, 2)


def _round_optional_ratio(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value, 3)


def _calculate_max_drawdown(values: List[float]) -> Optional[float]:
    if not values:
        return None
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        if value > peak:
            peak = value
        if peak <= 0:
            continue
        drawdown = (value / peak) - 1.0
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    return max_drawdown


def _build_performance_metrics(
    history: List[Dict[str, Any]],
) -> Dict[str, Optional[float]]:
    values = [
        float(point.get("v", 0.0))
        for point in history
        if point.get("v") is not None
    ]
    if len(values) < 2 or values[0] <= 0:
        return {
            "startValue": round(values[0], 2) if values else None,
            "endValue": round(values[-1], 2) if values else None,
            "totalReturnPct": 0.0 if values else None,
            "annualizedReturnPct": None,
            "volatilityPct": None,
            "sharpe": None,
            "maxDrawdownPct": None,
            "calmar": None,
        }

    daily_returns = []
    for prev_value, curr_value in zip(values[:-1], values[1:]):
        if prev_value <= 0:
            continue
        daily_returns.append((curr_value / prev_value) - 1.0)

    total_return = (values[-1] / values[0]) - 1.0
    num_periods = len(daily_returns)
    annualized_return = None
    volatility = None
    sharpe = None
    calmar = None

    if num_periods > 0:
        annualized_return = (
            (values[-1] / values[0]) ** (TRADING_DAYS_PER_YEAR / num_periods)
        ) - 1.0

    if len(daily_returns) >= 2:
        mean_daily = sum(daily_returns) / len(daily_returns)
        variance = sum(
            (daily_return - mean_daily) ** 2 for daily_return in daily_returns
        ) / (len(daily_returns) - 1)
        volatility = math.sqrt(max(variance, 0.0)) * math.sqrt(
            TRADING_DAYS_PER_YEAR,
        )
        if volatility > 0:
            sharpe = (mean_daily * TRADING_DAYS_PER_YEAR) / volatility

    max_drawdown = _calculate_max_drawdown(values)
    if annualized_return is not None and max_drawdown not in (None, 0):
        calmar = annualized_return / abs(max_drawdown)

    return {
        "startValue": round(values[0], 2),
        "endValue": round(values[-1], 2),
        "totalReturnPct": round(total_return * 100, 2),
        "annualizedReturnPct": _round_optional_percent(annualized_return),
        "volatilityPct": _round_optional_percent(volatility),
        "sharpe": _round_optional_ratio(sharpe),
        "maxDrawdownPct": _round_optional_percent(max_drawdown),
        "calmar": _round_optional_ratio(calmar),
    }


def _build_backtest_period(
    equity_history: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not equity_history:
        return {"startDate": None, "endDate": None, "tradingDays": 0}

    return {
        "startDate": datetime.fromtimestamp(
            equity_history[0]["t"] / 1000,
        ).strftime("%Y-%m-%d"),
        "endDate": datetime.fromtimestamp(
            equity_history[-1]["t"] / 1000,
        ).strftime("%Y-%m-%d"),
        "tradingDays": len(equity_history) - 1 if len(equity_history) > 1 else 0,
    }


def build_stats_from_summary(
    summary: Dict[str, Any],
    existing_stats: Dict[str, Any],
) -> Dict[str, Any]:
    equity = summary.get("equity", [])
    baseline = summary.get("baseline", [])
    baseline_vw = summary.get("baseline_vw", [])
    momentum = summary.get("momentum", [])

    agent = _build_performance_metrics(equity)
    equal_weight = _build_performance_metrics(baseline)
    market_cap_weighted = _build_performance_metrics(baseline_vw)
    momentum_metrics = _build_performance_metrics(momentum)

    return {
        "totalAssetValue": summary.get("totalAssetValue"),
        "totalReturn": summary.get("totalReturn"),
        "cashPosition": summary.get("cashPosition"),
        "tickerWeights": summary.get("tickerWeights", {}),
        "totalTrades": summary.get("totalTrades", 0),
        "winRate": existing_stats.get("winRate", 0.0),
        "bullBear": existing_stats.get(
            "bullBear",
            {
                "bull": {"n": 0, "win": 0},
                "bear": {"n": 0, "win": 0},
            },
        ),
        "period": _build_backtest_period(equity),
        "performance": {
            "agent": agent,
            "benchmarks": {
                "equalWeight": equal_weight,
                "marketCapWeighted": market_cap_weighted,
                "momentum": momentum_metrics,
            },
            "comparison": {
                "equalWeight": {
                    "excessReturnPct": _safe_diff(
                        agent.get("totalReturnPct"),
                        equal_weight.get("totalReturnPct"),
                    ),
                    "sharpeSpread": _safe_diff(
                        agent.get("sharpe"),
                        equal_weight.get("sharpe"),
                    ),
                },
                "marketCapWeighted": {
                    "excessReturnPct": _safe_diff(
                        agent.get("totalReturnPct"),
                        market_cap_weighted.get("totalReturnPct"),
                    ),
                    "sharpeSpread": _safe_diff(
                        agent.get("sharpe"),
                        market_cap_weighted.get("sharpe"),
                    ),
                },
                "momentum": {
                    "excessReturnPct": _safe_diff(
                        agent.get("totalReturnPct"),
                        momentum_metrics.get("totalReturnPct"),
                    ),
                    "sharpeSpread": _safe_diff(
                        agent.get("sharpe"),
                        momentum_metrics.get("sharpe"),
                    ),
                },
            },
        },
    }


def normalize_leaderboard(leaderboard: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    ranking_entries = []

    for entry in leaderboard:
        entry.setdefault("qualityScores", {})
        entry["qualityScores"].setdefault("rm", None)
        entry["qualityScores"].setdefault("grounding", None)
        entry["qualityScores"].setdefault("audit", None)
        entry["qualityScores"].setdefault("presentation", None)
        entry["qualityScores"].setdefault("overall", None)
        entry["qualityScores"].setdefault("count", 0)

        entry.setdefault("qualityHistory", [])
        entry.setdefault("scoreBreakdown", {})
        entry["scoreBreakdown"].setdefault("winRate", entry.get("winRate"))
        entry["scoreBreakdown"].setdefault("rm", None)
        entry["scoreBreakdown"].setdefault("grounding", None)
        entry["scoreBreakdown"].setdefault("audit", None)
        entry["scoreBreakdown"].setdefault("presentation", None)
        entry["scoreBreakdown"].setdefault("overallQuality", None)

        if entry.get("weightedScore") is None and entry.get("winRate") is not None:
            entry["weightedScore"] = round(float(entry["winRate"]), 4)

        entry.setdefault("performanceHistory", [])
        if (
            entry.get("rank") is not None
            and entry.get("weightedScore") is not None
            and not entry["performanceHistory"]
        ):
            signal_dates = [
                signal.get("date")
                for signal in entry.get("signals", [])
                if signal.get("date")
            ]
            snapshot_date = max(signal_dates) if signal_dates else None
            entry["performanceHistory"].append(
                {
                    "date": snapshot_date,
                    "weighted_score": entry.get("weightedScore"),
                    "win_rate": entry.get("winRate"),
                    "rm": entry["scoreBreakdown"].get("rm"),
                    "grounding": entry["scoreBreakdown"].get("grounding"),
                    "audit": entry["scoreBreakdown"].get("audit"),
                    "presentation": entry["scoreBreakdown"].get("presentation"),
                },
            )

        if entry.get("rank") is not None:
            ranking_entries.append(entry)
        normalized.append(entry)

    ranking_entries.sort(
        key=lambda item: (
            item.get("weightedScore") is None,
            -(item.get("weightedScore") or -1),
            -(item.get("winRate") or -1),
        ),
    )
    for idx, entry in enumerate(ranking_entries, start=1):
        entry["rank"] = idx

    return normalized


def load_report_builder():
    script_path = Path(__file__).resolve().parent / "generate_backtest_report.py"
    spec = spec_from_file_location("generate_backtest_report", script_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def build_milestone_report(
    stats: Dict[str, Any],
    leaderboard: List[Dict[str, Any]],
    report_path: Path,
) -> Dict[str, Any]:
    agent_perf = stats.get("performance", {}).get("agent", {})
    required_metric_keys = [
        "annualizedReturnPct",
        "sharpe",
        "maxDrawdownPct",
        "calmar",
    ]
    metrics_present = all(key in agent_perf for key in required_metric_keys)
    comparison = stats.get("performance", {}).get("comparison", {})
    comparison_present = all(
        benchmark in comparison
        and "excessReturnPct" in comparison[benchmark]
        for benchmark in [
            "equalWeight",
            "marketCapWeighted",
            "momentum",
        ]
    )
    ranked_agents = [
        entry
        for entry in leaderboard
        if entry.get("rank") is not None and entry.get("weightedScore") is not None
    ]
    history_ready = any(
        entry.get("performanceHistory")
        for entry in leaderboard
        if entry.get("rank") is not None
    )

    return {
        "milestone": "Day 21 acceptance",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "checks": {
            "reportGenerated": report_path.exists(),
            "quantMetricsPresent": metrics_present,
            "benchmarkComparisonPresent": comparison_present,
            "leaderboardSchemaReady": all(
                "scoreBreakdown" in entry and "performanceHistory" in entry
                for entry in leaderboard
            ),
            "leaderboardRankedAgents": len(ranked_agents),
            "leaderboardHistoryReady": history_ready,
        },
        "highlights": {
            "agentAnnualizedReturnPct": agent_perf.get("annualizedReturnPct"),
            "agentSharpe": agent_perf.get("sharpe"),
            "agentMaxDrawdownPct": agent_perf.get("maxDrawdownPct"),
            "agentCalmar": agent_perf.get("calmar"),
            "equalWeightExcessReturnPct": comparison.get("equalWeight", {}).get(
                "excessReturnPct",
            ),
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Run Week 3 acceptance.")
    parser.add_argument(
        "--dashboard-dir",
        required=True,
        help="Path to team_dashboard directory.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory for milestone report. Defaults to config root.",
    )
    args = parser.parse_args()

    dashboard_dir = Path(args.dashboard_dir)
    config_root = (
        Path(args.output_dir)
        if args.output_dir
        else dashboard_dir.parent
    )

    summary = _load_json(dashboard_dir / "summary.json", {})
    stats = _load_json(dashboard_dir / "stats.json", {})
    trades = _load_json(dashboard_dir / "trades.json", [])
    leaderboard = _load_json(dashboard_dir / "leaderboard.json", [])

    refreshed_stats = build_stats_from_summary(summary, stats)
    normalized_leaderboard = normalize_leaderboard(leaderboard)

    _save_json(dashboard_dir / "stats.json", refreshed_stats)
    _save_json(dashboard_dir / "leaderboard.json", normalized_leaderboard)

    report_module = load_report_builder()
    report_path = dashboard_dir / "backtest_report.html"
    report_html = report_module.build_report_html(
        dashboard_dir=dashboard_dir,
        summary=summary,
        stats=refreshed_stats,
        trades=trades,
        leaderboard=normalized_leaderboard,
    )
    report_path.write_text(report_html, encoding="utf-8")

    milestone_report = build_milestone_report(
        refreshed_stats,
        normalized_leaderboard,
        report_path,
    )
    output_path = config_root / "week3_milestone_report.json"
    _save_json(output_path, milestone_report)

    print("=== Week 3 Acceptance ===")
    print(f"Dashboard: {dashboard_dir}")
    print(f"Report: {report_path}")
    print(f"Milestone: {output_path}")
    print(
        "Checks:",
        json.dumps(milestone_report["checks"], ensure_ascii=False),
    )


if __name__ == "__main__":
    main()
