from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any


def build_decision_audits(
    *,
    run_id: int,
    strategy_id: str,
    run_date: date,
    decisions: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    execution_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    signal_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for signal in signals:
        signal_by_symbol[str(signal.get("symbol"))].append(signal)

    execution_by_symbol = {
        str(row.get("symbol")): row
        for row in execution_checks
    }
    audits: list[dict[str, Any]] = []
    for decision in decisions:
        symbol = str(decision.get("symbol") or "")
        action = str(decision.get("action") or "hold").lower()
        symbol_signals = signal_by_symbol.get(symbol, [])
        scores = [float(item.get("score") or 0.0) for item in symbol_signals]
        confidences = [float(item.get("confidence") or 0.0) for item in symbol_signals]

        if _has_disagreement(scores):
            audits.append(
                _audit_row(
                    run_id,
                    strategy_id,
                    run_date,
                    symbol,
                    action,
                    "analyst_disagreement",
                    "medium",
                    "分析师信号方向分歧，PM 决策需要重点复盘。",
                    {"scores": scores, "confidences": confidences},
                ),
            )

        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        avg_score = sum(scores) / len(scores) if scores else 0.0
        if action in {"buy", "long", "increase"} and avg_confidence >= 75 and avg_score <= 0:
            audits.append(
                _audit_row(
                    run_id,
                    strategy_id,
                    run_date,
                    symbol,
                    action,
                    "high_confidence_conflict",
                    "high",
                    "PM 给出买入/加仓，但分析师平均信号不支持，属于高置信冲突样本。",
                    {"avg_score": avg_score, "avg_confidence": avg_confidence},
                ),
            )

        check = execution_by_symbol.get(symbol)
        if check and not check.get("approved", True):
            audits.append(
                _audit_row(
                    run_id,
                    strategy_id,
                    run_date,
                    symbol,
                    action,
                    "execution_blocked",
                    "high",
                    "PM 决策被 A 股交易约束或容量约束拦截。",
                    {
                        "rejection_reason": check.get("rejection_reason"),
                        "warnings": check.get("warnings"),
                        "capacity_ratio": check.get("capacity_ratio"),
                    },
                ),
            )

        if not str(decision.get("reasoning") or "").strip():
            audits.append(
                _audit_row(
                    run_id,
                    strategy_id,
                    run_date,
                    symbol,
                    action,
                    "missing_reasoning",
                    "low",
                    "PM 决策缺少可复盘理由。",
                    {},
                ),
            )
    return audits


def _has_disagreement(scores: list[float]) -> bool:
    positive = any(score > 0.15 for score in scores)
    negative = any(score < -0.15 for score in scores)
    return positive and negative


def _audit_row(
    run_id: int,
    strategy_id: str,
    run_date: date,
    symbol: str,
    action: str,
    audit_type: str,
    severity: str,
    detail: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "date": run_date,
        "symbol": symbol,
        "action": action,
        "audit_type": audit_type,
        "severity": severity,
        "detail": detail,
        "evidence": evidence,
    }
