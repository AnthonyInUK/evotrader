# -*- coding: utf-8 -*-
"""Replay the structured decision layer from saved backtest artifacts.

This is a no-LLM diagnostic. It reconstructs analyst signals, prices, and the
portfolio state before each PM decision, then asks the deterministic structured
decision layer what it would have ranked and sized.
"""
from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backend.core.pipeline import TradingPipeline
from replay_pm_compact_context import (  # noqa: E402
    ANALYST_AGENTS,
    _date_range,
    _extract_sections,
)


class _DummyPM:
    def get_portfolio_state(self) -> dict[str, Any]:
        return {"cash": 0, "positions": {}}


def _default_dates(config_dir: Path) -> list[str]:
    nav_path = config_dir / "nav_curve.csv"
    if nav_path.exists():
        dates = []
        for line in nav_path.read_text(encoding="utf-8").splitlines()[1:]:
            if line.strip():
                dates.append(line.split(",", 1)[0])
        return dates

    daily_dir = config_dir / "logs" / "daily"
    if not daily_dir.exists():
        return []
    return [
        path.name.removesuffix("_reasoning.txt")
        for path in sorted(daily_dir.glob("*_reasoning.txt"))
    ]


def _load_internal_state(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "team_dashboard" / "_internal_state.json"
    if not path.exists():
        raise SystemExit(f"Missing internal state: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _load_leaderboard_signals(config_dir: Path) -> dict[tuple[str, str], dict[str, str]]:
    path = config_dir / "team_dashboard" / "leaderboard.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    by_agent_date: dict[tuple[str, str], dict[str, str]] = {}
    for agent in data:
        agent_id = agent.get("agentId")
        if agent_id not in ANALYST_AGENTS:
            continue
        for row in agent.get("signals", []):
            date_value = row.get("date")
            ticker = str(row.get("ticker", "")).upper()
            signal = str(row.get("signal", "neutral")).upper()
            if signal == "UP":
                signal = "BULL"
            elif signal == "DOWN":
                signal = "BEAR"
            elif signal in {"BULLISH"}:
                signal = "BULL"
            elif signal in {"BEARISH"}:
                signal = "BEAR"
            elif signal not in {"BULL", "BEAR", "NEUTRAL"}:
                signal = "NEUTRAL"
            by_agent_date.setdefault((agent_id, date_value), {})[ticker] = signal
    return by_agent_date


def _leaderboard_analyst_results(
    signals: dict[tuple[str, str], dict[str, str]],
    day: str,
    tickers: list[str],
) -> list[dict[str, Any]]:
    results = []
    for agent in ANALYST_AGENTS:
        rows = []
        values = signals.get((agent, day), {})
        for ticker in tickers:
            signal = values.get(ticker, "NEUTRAL")
            rows.append(
                f"SIGNAL: {signal} | CONFIDENCE: 60 | TICKER: {ticker}"
            )
        results.append({"agent": agent, "content": "\n".join(rows)})
    return results


def _prices_by_date(state: dict[str, Any], day: str) -> dict[str, float]:
    prices = {}
    for ticker, rows in state.get("price_history", {}).items():
        for row in rows:
            if row.get("date") == day:
                prices[ticker] = float(row.get("price") or 0)
                break
    return prices


def _extract_analyst_results(config_dir: Path, day: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = config_dir / "logs" / "daily" / f"{day}_reasoning.txt"
    if not path.exists():
        raise SystemExit(f"Missing reasoning log: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = _extract_sections(text)
    analyst_results = [
        {"agent": agent, "content": sections.get(agent, "")}
        for agent in ANALYST_AGENTS
    ]
    risk_assessment = {"content": sections.get("risk_manager", "")}
    return analyst_results, risk_assessment


def _extract_risk_assessment(config_dir: Path, day: str) -> dict[str, Any]:
    path = config_dir / "logs" / "daily" / f"{day}_reasoning.txt"
    if not path.exists():
        return {"content": ""}
    text = path.read_text(encoding="utf-8", errors="replace")
    sections = _extract_sections(text)
    return {"content": sections.get("risk_manager", "")}


def _normalize_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    state = {"cash": float(portfolio.get("cash", 0) or 0), "positions": {}}
    for ticker, pos in (portfolio.get("positions", {}) or {}).items():
        if isinstance(pos, dict):
            qty = int(pos.get("long", pos.get("quantity", 0)) or 0)
            cost = float(pos.get("long_cost_basis", 0) or 0)
        else:
            qty = int(pos or 0)
            cost = 0.0
        if qty > 0:
            state["positions"][ticker] = {
                "long": qty,
                "long_cost_basis": cost,
                "short": 0,
                "short_cost_basis": 0.0,
            }
    return state


def _infer_initial_cash(state: dict[str, Any]) -> float:
    history = state.get("equity_history", [])
    if history:
        return float(history[0].get("v") or 0)
    return 500000.0


def _trades_by_date(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for trade in state.get("all_trades", []):
        grouped.setdefault(trade.get("trading_date"), []).append(trade)
    for trades in grouped.values():
        trades.sort(key=lambda item: item.get("id", ""))
    return grouped


def _portfolio_before_dates(
    initial_cash: float,
    dates: list[str],
    trades_by_date: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, Any]]:
    portfolio = {"cash": initial_cash, "positions": {}}
    before: dict[str, dict[str, Any]] = {}
    for day in dates:
        before[day] = deepcopy(portfolio)
        for trade in trades_by_date.get(day, []):
            ticker = trade["ticker"]
            qty = int(trade.get("qty") or 0)
            price = float(trade.get("price") or 0)
            side = str(trade.get("side", "")).upper()
            pos = portfolio["positions"].setdefault(
                ticker,
                {"long": 0, "long_cost_basis": 0.0, "short": 0, "short_cost_basis": 0.0},
            )
            if side == "LONG":
                old_qty = int(pos.get("long", 0) or 0)
                old_cost = float(pos.get("long_cost_basis", 0) or 0)
                new_qty = old_qty + qty
                if new_qty > 0:
                    pos["long_cost_basis"] = (
                        old_qty * old_cost + qty * price
                    ) / new_qty
                pos["long"] = new_qty
                portfolio["cash"] -= qty * price
            elif side == "SHORT":
                sell_qty = min(qty, int(pos.get("long", 0) or 0))
                pos["long"] = int(pos.get("long", 0) or 0) - sell_qty
                portfolio["cash"] += sell_qty * price
    return before


def _actual_trade_summary(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for trade in trades:
        item = summary.setdefault(
            trade["ticker"],
            {"long_qty": 0, "sell_qty": 0, "net_qty": 0, "value": 0.0},
        )
        qty = int(trade.get("qty") or 0)
        price = float(trade.get("price") or 0)
        if str(trade.get("side", "")).upper() == "LONG":
            item["long_qty"] += qty
            item["net_qty"] += qty
            item["value"] += qty * price
        else:
            item["sell_qty"] += qty
            item["net_qty"] -= qty
            item["value"] -= qty * price
    return summary


def replay_day(
    pipeline: TradingPipeline,
    config_dir: Path,
    state: dict[str, Any],
    day: str,
    tickers: list[str],
    portfolio: dict[str, Any],
    trades: list[dict[str, Any]],
    leaderboard_signals: dict[tuple[str, str], dict[str, str]],
    signal_source: str,
) -> dict[str, Any]:
    prices = _prices_by_date(state, day)
    if signal_source == "leaderboard" and leaderboard_signals:
        analyst_results = _leaderboard_analyst_results(
            leaderboard_signals,
            day,
            tickers,
        )
        risk_assessment = _extract_risk_assessment(config_dir, day)
    else:
        analyst_results, risk_assessment = _extract_analyst_results(config_dir, day)
    structured = pipeline._build_structured_decision_layer(
        tickers,
        analyst_results,
        risk_assessment,
        prices,
        portfolio,
    )
    return {
        "date": day,
        "prices": prices,
        "portfolio_before": portfolio,
        "signal_source": (
            "leaderboard"
            if signal_source == "leaderboard" and leaderboard_signals
            else "reasoning_log"
        ),
        "structured": structured,
        "actual_trades": _actual_trade_summary(trades),
    }


def _print_report(result: dict[str, Any], top_n: int) -> None:
    print("=== Structured Decision Replay ===")
    print(f"Config: {result['configName']}")
    print(f"Dates: {', '.join(result['datesChecked'])}")
    print(f"Tickers: {', '.join(result['tickers'])}")
    print()

    for day in result["days"]:
        structured = day["structured"]
        print(f"## {day['date']}")
        print(f"Signal source: {day['signal_source']}")
        print(
            "Regime: "
            f"{structured['regime']} target="
            f"{structured['target_exposure_band']['low_pct']:.0f}-"
            f"{structured['target_exposure_band']['high_pct']:.0f}% "
            f"current={structured['current_exposure_pct']:.2f}% "
            f"gap={structured['deployment_gap_pct']:.2f}% "
            f"value={structured['deployment_gap_value']:.2f}"
        )
        print("Top structured candidates:")
        for row in structured["candidate_scores"][:top_n]:
            trade = day["actual_trades"].get(row["ticker"], {})
            actual = ""
            if trade:
                actual = (
                    f" actual_net={trade['net_qty']} "
                    f"actual_value={trade['value']:.0f}"
                )
            print(
                "  "
                f"#{row['rank']} {row['ticker']} {row['role']} "
                f"score={row['score']:.1f} "
                f"cur={row['current_weight_pct']:.1f}% "
                f"sug={row['suggested_weight_pct']:.1f}% "
                f"delta={row['suggested_delta_pct']:.1f}% "
                f"qty={row['suggested_qty']}"
                f"{actual}"
            )
        if day["actual_trades"]:
            print("Actual trades:")
            for ticker, item in day["actual_trades"].items():
                print(
                    "  "
                    f"{ticker}: net={item['net_qty']} "
                    f"long={item['long_qty']} sell={item['sell_qty']} "
                    f"value={item['value']:.0f}"
                )
        else:
            print("Actual trades: none")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay structured decision scoring without calling any LLM.",
    )
    parser.add_argument("config_name")
    parser.add_argument("--date", action="append")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument(
        "--signal-source",
        choices=["leaderboard", "reasoning-log"],
        default="leaderboard",
    )
    parser.add_argument("--json-out")
    args = parser.parse_args()

    config_dir = Path(args.config_name)
    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    dates = args.date or _date_range(args.start, args.end) or _default_dates(config_dir)
    if not dates:
        raise SystemExit("No dates found. Pass --date or --start/--end.")

    state = _load_internal_state(config_dir)
    leaderboard_signals = _load_leaderboard_signals(config_dir)
    trades = _trades_by_date(state)
    initial_cash = _infer_initial_cash(state)
    portfolios = _portfolio_before_dates(initial_cash, dates, trades)
    pipeline = TradingPipeline([], None, _DummyPM(), config_name=str(config_dir))

    days = [
        replay_day(
            pipeline,
            config_dir,
            state,
            day,
            tickers,
            portfolios[day],
            trades.get(day, []),
            leaderboard_signals,
            args.signal_source,
        )
        for day in dates
    ]
    result = {
        "configName": args.config_name,
        "tickers": tickers,
        "datesChecked": dates,
        "days": days,
    }

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    _print_report(result, args.top)


if __name__ == "__main__":
    main()
