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

from backend.data.historical_price_manager import HistoricalPriceManager
from config.strategy_loader import load_strategy
from execution.checks import build_execution_checks
from pipeline.universe_selection import run_universe_selection


def run_experiment(
    *,
    strategy_path: str,
    start_date: date,
    end_date: date,
    portfolio_value: float,
    raw_target_weight: float,
    output_dir: Path,
) -> dict[str, Any]:
    config = load_strategy(strategy_path)
    daily_reports: list[dict[str, Any]] = []
    all_orders: list[dict[str, Any]] = []
    all_checks: list[dict[str, Any]] = []

    for run_date in _weekday_dates(start_date, end_date):
        selection = run_universe_selection(config, run_date)
        market = _load_market(selection.selected_symbols, run_date)
        raw_orders = _build_raw_orders(
            strategy_id=config.strategy_id,
            run_date=run_date,
            symbols=selection.selected_symbols,
            market=market,
            portfolio_value=portfolio_value,
            raw_target_weight=raw_target_weight,
        )
        risk_adjusted = _apply_position_cap(
            raw_orders,
            portfolio_value=portfolio_value,
            max_position_size=config.risk_limits.max_position_size,
        )
        checks = build_execution_checks(
            run_id=0,
            strategy_id=config.strategy_id,
            run_date=run_date,
            decisions=risk_adjusted,
            market=market,
        )
        joined = _join_orders_and_checks(raw_orders, risk_adjusted, checks)
        all_orders.extend(joined)
        all_checks.extend(checks)
        daily_reports.append(
            {
                "date": run_date.isoformat(),
                "selected_symbols": selection.selected_symbols,
                "summary": _summarize(joined),
                "orders": joined,
            },
        )

    report = {
        "experiment": "risk_execution_ablation",
        "strategy_id": config.strategy_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "trading_days": len(daily_reports),
        "portfolio_value": portfolio_value,
        "raw_target_weight": raw_target_weight,
        "max_position_size": config.risk_limits.max_position_size,
        "aggregate": _summarize(all_orders),
        "daily": daily_reports,
    }
    _write_outputs(report, all_orders, output_dir)
    return report


def _weekday_dates(start_date: date, end_date: date) -> list[date]:
    dates = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def _load_market(symbols: list[str], run_date: date) -> dict[str, Any]:
    price_mgr = HistoricalPriceManager()
    price_mgr.subscribe(symbols)
    price_mgr.preload_data(
        (run_date - timedelta(days=30)).isoformat(),
        run_date.isoformat(),
    )
    price_mgr.set_date(run_date.isoformat())

    open_prices: dict[str, float] = {}
    prev_closes: dict[str, float] = {}
    volumes: dict[str, int] = {}
    for symbol in symbols:
        open_price = price_mgr.get_open_price(symbol)
        if open_price is None:
            continue
        open_prices[symbol] = float(open_price)
        df = price_mgr._price_cache.get(symbol)
        prev_close = float(open_price)
        volume = 0
        if df is not None and not df.empty:
            frame = df.copy()
            if "date" in frame.columns:
                frame["_date"] = frame["date"].astype(str).str[:10]
            elif "time" in frame.columns:
                frame["_date"] = frame["time"].astype(str).str[:10]
            else:
                frame["_date"] = frame.index.astype(str).str[:10]
            today = frame[frame["_date"] == run_date.isoformat()]
            prior = frame[frame["_date"] < run_date.isoformat()]
            if not today.empty:
                volume = int(today.iloc[-1].get("volume", 0) or 0)
            if not prior.empty:
                prev_close = float(prior.iloc[-1].get("close", open_price))
        prev_closes[symbol] = prev_close
        volumes[symbol] = volume
    return {
        "open_prices": open_prices,
        "prev_closes": prev_closes,
        "volumes": volumes,
    }


def _build_raw_orders(
    *,
    strategy_id: str,
    run_date: date,
    symbols: list[str],
    market: dict[str, Any],
    portfolio_value: float,
    raw_target_weight: float,
) -> list[dict[str, Any]]:
    rows = []
    target_notional = portfolio_value * raw_target_weight
    for symbol in symbols:
        price = float(market["open_prices"].get(symbol) or 0.0)
        if price <= 0:
            continue
        quantity = max(1, int(target_notional / price))
        rows.append(
            {
                "run_id": 0,
                "strategy_id": strategy_id,
                "date": run_date,
                "symbol": symbol,
                "action": "buy",
                "quantity": quantity,
                "raw_target_weight": raw_target_weight,
                "risk_target_weight": raw_target_weight,
                "reasoning": "Selector-implied buy intent for execution ablation.",
                "agent_votes": {"quantity": quantity},
            },
        )
    return rows


def _apply_position_cap(
    orders: list[dict[str, Any]],
    *,
    portfolio_value: float,
    max_position_size: float,
) -> list[dict[str, Any]]:
    adjusted = []
    max_notional = portfolio_value * max_position_size
    for order in orders:
        symbol = order["symbol"]
        quantity = int(order["quantity"])
        # price is reconstructed from raw target notional / quantity.
        target_notional = portfolio_value * float(order["raw_target_weight"])
        implied_price = target_notional / quantity if quantity else 0.0
        capped_quantity = quantity
        risk_target_weight = float(order["raw_target_weight"])
        risk_warning = ""
        if target_notional > max_notional and implied_price > 0:
            capped_quantity = max(1, int(max_notional / implied_price))
            risk_target_weight = max_position_size
            risk_warning = (
                f"raw target weight {order['raw_target_weight']:.2%} exceeds "
                f"max position size {max_position_size:.2%}; quantity capped"
            )
        adjusted.append(
            {
                **order,
                "quantity": capped_quantity,
                "risk_target_weight": risk_target_weight,
                "risk_adjusted": capped_quantity != quantity,
                "risk_warning": risk_warning,
                "agent_votes": {"quantity": capped_quantity},
                "raw_quantity": quantity,
            },
        )
    return adjusted


def _join_orders_and_checks(
    raw_orders: list[dict[str, Any]],
    risk_orders: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_by_symbol = {row["symbol"]: row for row in raw_orders}
    risk_by_symbol = {row["symbol"]: row for row in risk_orders}
    joined = []
    for check in checks:
        symbol = check["symbol"]
        raw = raw_by_symbol[symbol]
        risk = risk_by_symbol[symbol]
        adjusted_quantity = int(check["adjusted_quantity"])
        raw_quantity = int(raw["quantity"])
        risk_quantity = int(risk["quantity"])
        joined.append(
            {
                "date": str(check["date"]),
                "symbol": symbol,
                "action": check["action"],
                "raw_quantity": raw_quantity,
                "risk_quantity": risk_quantity,
                "execution_quantity": adjusted_quantity,
                "risk_adjusted": bool(risk.get("risk_adjusted")),
                "execution_adjusted": adjusted_quantity != risk_quantity,
                "approved": bool(check["approved"]),
                "rejection_reason": check["rejection_reason"],
                "warnings": check["warnings"],
                "transaction_cost": check["transaction_cost"],
                "capacity_ratio": check["capacity_ratio"],
                "slippage_estimate": check["slippage_estimate"],
                "risk_warning": risk.get("risk_warning", ""),
            },
        )
    return joined


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "raw_orders": 0,
            "risk_adjusted": 0,
            "execution_adjusted": 0,
            "blocked": 0,
            "total_transaction_cost": 0.0,
            "avg_capacity_ratio": 0.0,
            "avg_slippage_estimate": 0.0,
        }
    capacity = [float(row["capacity_ratio"]) for row in rows if row.get("capacity_ratio") is not None]
    slippage = [float(row["slippage_estimate"]) for row in rows if row.get("slippage_estimate") is not None]
    return {
        "raw_orders": len(rows),
        "risk_adjusted": sum(1 for row in rows if row["risk_adjusted"]),
        "execution_adjusted": sum(1 for row in rows if row["execution_adjusted"]),
        "blocked": sum(1 for row in rows if not row["approved"]),
        "total_transaction_cost": round(sum(float(row["transaction_cost"]) for row in rows), 2),
        "avg_capacity_ratio": _mean(capacity),
        "max_capacity_ratio": round(max(capacity), 6) if capacity else 0.0,
        "avg_slippage_estimate": _mean(slippage),
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0


def _write_outputs(report: dict[str, Any], rows: list[dict[str, Any]], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    key = f"{report['strategy_id']}_{report['start_date'].replace('-', '')}_{report['end_date'].replace('-', '')}"
    (output_dir / f"{key}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    columns = [
        "date",
        "symbol",
        "raw_quantity",
        "risk_quantity",
        "execution_quantity",
        "risk_adjusted",
        "execution_adjusted",
        "approved",
        "transaction_cost",
        "capacity_ratio",
        "slippage_estimate",
        "risk_warning",
        "warnings",
    ]
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row.get(column, "")).replace(",", ";") for column in columns))
    (output_dir / f"{key}_orders.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RiskGuard and A-share execution ablation.")
    parser.add_argument("--strategy", default="strategies/momentum_v1.yaml")
    parser.add_argument("--start-date", default="2024-02-20")
    parser.add_argument("--end-date", default="2024-02-26")
    parser.add_argument("--portfolio-value", type=float, default=500000.0)
    parser.add_argument("--raw-target-weight", type=float, default=0.35)
    parser.add_argument("--output-dir", default="outputs/experiments/execution_ablation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = run_experiment(
        strategy_path=args.strategy,
        start_date=date.fromisoformat(args.start_date),
        end_date=date.fromisoformat(args.end_date),
        portfolio_value=args.portfolio_value,
        raw_target_weight=args.raw_target_weight,
        output_dir=ROOT / args.output_dir,
    )
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
