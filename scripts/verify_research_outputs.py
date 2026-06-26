#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.data.historical_price_manager import HistoricalPriceManager
from backend.utils.a_share_constraints import AShareConstraints, calc_transaction_cost
from config.strategy_loader import load_strategy
from execution.checks import build_execution_checks
from pipeline.universe_selection import run_universe_selection
from research.decision_audit import build_decision_audits
from research.selection_attribution import build_selection_attribution


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any]


def verify_forward_returns(
    strategy_path: str,
    run_date: date,
    sample_size: int,
    tolerance: float,
) -> list[CheckResult]:
    config = load_strategy(strategy_path)
    selection = run_universe_selection(config, run_date)
    attribution = build_selection_attribution(
        run_id=0,
        strategy_id=config.strategy_id,
        run_date=run_date,
        selection_rows=selection.candidate_rows,
    )

    symbols = [row["symbol"] for row in attribution[:sample_size]]
    price_mgr = HistoricalPriceManager()
    price_mgr.subscribe(symbols)
    price_mgr.preload_data(
        (run_date - timedelta(days=30)).isoformat(),
        (run_date + timedelta(days=25)).isoformat(),
    )

    checks: list[CheckResult] = []
    for row in attribution[:sample_size]:
        symbol = row["symbol"]
        manual = _manual_forward_returns(price_mgr, symbol, run_date, [1, 5, 10])
        comparisons = {}
        passed = True
        for horizon in [1, 5, 10]:
            key = f"forward_return_{horizon}d"
            expected = manual.get(horizon, {}).get("return")
            actual = row.get(key)
            delta = (
                abs(float(actual) - float(expected))
                if actual is not None and expected is not None
                else None
            )
            ok = actual == expected if delta is None else delta <= tolerance
            passed = passed and ok
            comparisons[key] = {
                "system": actual,
                "manual": expected,
                "delta": delta,
                "ok": ok,
                **manual.get(horizon, {}),
            }
        checks.append(
            CheckResult(
                name=f"forward_return::{symbol}",
                passed=passed,
                detail=(
                    "系统 forward return 与逐笔手算一致"
                    if passed
                    else "forward return 与手算不一致，需要检查交易日定位"
                ),
                evidence={
                    "symbol": symbol,
                    "rank": row["rank"],
                    "bucket": row["bucket"],
                    "run_date": run_date.isoformat(),
                    "comparisons": comparisons,
                },
            ),
        )
    return checks


def verify_execution_rules() -> list[CheckResult]:
    cases = [
        {
            "name": "buy_round_lot",
            "call": lambda: AShareConstraints.validate_buy_order(
                "600519.SH",
                quantity=150,
                price=10.0,
                prev_close=9.9,
                available_cash=10_000,
            ),
            "expect": lambda result: result.approved and result.adjusted_quantity == 100,
            "detail": "买入 150 股应按 A 股手数规则调整为 100 股",
        },
        {
            "name": "buy_less_than_one_lot",
            "call": lambda: AShareConstraints.validate_buy_order(
                "600519.SH",
                quantity=99,
                price=10.0,
                prev_close=9.9,
                available_cash=10_000,
            ),
            "expect": lambda result: not result.approved,
            "detail": "买入 99 股不足 1 手，应拒绝",
        },
        {
            "name": "limit_up_buy_block",
            "call": lambda: AShareConstraints.validate_buy_order(
                "600519.SH",
                quantity=100,
                price=11.0,
                prev_close=10.0,
                available_cash=10_000,
            ),
            "expect": lambda result: not result.approved,
            "detail": "涨停价买入应被拦截",
        },
        {
            "name": "limit_down_sell_block",
            "call": lambda: AShareConstraints.validate_sell_order(
                "600519.SH",
                quantity=100,
                price=9.0,
                prev_close=10.0,
                available_shares=100,
            ),
            "expect": lambda result: not result.approved,
            "detail": "跌停价卖出应被拦截",
        },
        {
            "name": "sell_fee_contains_stamp_duty",
            "call": lambda: calc_transaction_cost("sell", 100, 10.0, "600519.SH"),
            "expect": lambda result: result["stamp_duty"] > 0 and result["transfer_fee"] > 0,
            "detail": "沪市卖出应包含印花税和过户费",
        },
    ]

    checks: list[CheckResult] = []
    for case in cases:
        result = case["call"]()
        passed = bool(case["expect"](result))
        evidence = result if isinstance(result, dict) else result.__dict__
        checks.append(
            CheckResult(
                name=f"execution_rule::{case['name']}",
                passed=passed,
                detail=case["detail"],
                evidence=evidence,
            ),
        )
    return checks


def verify_audit_rules(run_date: date) -> list[CheckResult]:
    decisions = [
        {
            "run_id": 0,
            "strategy_id": "verification",
            "date": run_date,
            "symbol": "600519.SH",
            "action": "buy",
            "reasoning": "verification sample",
            "agent_votes": {"quantity": 100},
        },
    ]
    signals = [
        {
            "run_id": 0,
            "strategy_id": "verification",
            "date": run_date,
            "symbol": "600519.SH",
            "score": 0.7,
            "confidence": 82,
        },
        {
            "run_id": 0,
            "strategy_id": "verification",
            "date": run_date,
            "symbol": "600519.SH",
            "score": -0.4,
            "confidence": 78,
        },
    ]
    execution_checks = build_execution_checks(
        run_id=0,
        strategy_id="verification",
        run_date=run_date,
        decisions=decisions,
        market={
            "open_prices": {"600519.SH": 10.0},
            "prev_closes": {"600519.SH": 9.9},
            "volumes": {"600519.SH": 10_000},
        },
    )
    audits = build_decision_audits(
        run_id=0,
        strategy_id="verification",
        run_date=run_date,
        decisions=decisions,
        signals=signals,
        execution_checks=execution_checks,
    )
    has_disagreement = any(row["audit_type"] == "analyst_disagreement" for row in audits)
    return [
        CheckResult(
            name="decision_audit::analyst_disagreement",
            passed=has_disagreement,
            detail="正负方向信号同时存在时，应生成分析师分歧审计样本",
            evidence={"audits": audits, "signals": signals},
        ),
    ]


def _manual_forward_returns(
    price_mgr: HistoricalPriceManager,
    symbol: str,
    run_date: date,
    horizons: list[int],
) -> dict[int, dict[str, Any]]:
    df = price_mgr._price_cache.get(symbol)
    if df is None or df.empty:
        return {horizon: {"return": None, "reason": "missing_price_cache"} for horizon in horizons}
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
        return {horizon: {"return": None, "reason": "run_date_not_found"} for horizon in horizons}
    start_idx = matches[-1]
    start_close = float(frame.loc[start_idx, "close"])

    result = {}
    for horizon in horizons:
        end_idx = start_idx + horizon
        if end_idx >= len(frame):
            result[horizon] = {"return": None, "reason": "target_date_not_found"}
            continue
        end_close = float(frame.loc[end_idx, "close"])
        result[horizon] = {
            "return": round(end_close / start_close - 1, 6),
            "start_date": frame.loc[start_idx, "_date"],
            "target_date": frame.loc[end_idx, "_date"],
            "start_close": start_close,
            "target_close": end_close,
            "formula": "target_close / start_close - 1",
        }
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify research outputs with hand-checkable samples.")
    parser.add_argument("--strategy", default="strategies/momentum_v1.yaml")
    parser.add_argument("--date", default="2024-02-23")
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--tolerance", type=float, default=1e-9)
    parser.add_argument("--json-out", default="outputs/research_verification/report_20240223.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_date = date.fromisoformat(args.date)
    checks = [
        *verify_forward_returns(args.strategy, run_date, args.sample_size, args.tolerance),
        *verify_execution_rules(),
        *verify_audit_rules(run_date),
    ]
    report = {
        "date": args.date,
        "strategy": args.strategy,
        "passed": all(check.passed for check in checks),
        "summary": {
            "total": len(checks),
            "passed": sum(1 for check in checks if check.passed),
            "failed": sum(1 for check in checks if not check.passed),
        },
        "checks": [asdict(check) for check in checks],
    }
    out_path = ROOT / args.json_out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    print(f"report: {out_path}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
