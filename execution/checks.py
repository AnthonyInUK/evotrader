from __future__ import annotations

from datetime import date
from typing import Any

from backend.utils.a_share_constraints import AShareConstraints


BUY_ACTIONS = {"buy", "long", "open_long", "increase"}
SELL_ACTIONS = {"sell", "short", "close", "reduce"}


def build_execution_checks(
    *,
    run_id: int,
    strategy_id: str,
    run_date: date,
    decisions: list[dict[str, Any]],
    market: dict[str, Any],
) -> list[dict[str, Any]]:
    open_prices = market.get("open_prices", {})
    prev_closes = market.get("prev_closes", {})
    volumes = market.get("volumes", {})
    rows: list[dict[str, Any]] = []

    for decision in decisions:
        symbol = str(decision.get("symbol") or "")
        action = str(decision.get("action") or "hold").lower()
        quantity = _extract_quantity(decision)
        price = float(open_prices.get(symbol) or 0.0)
        prev_close = float(prev_closes.get(symbol) or price or 0.0)
        volume = int(volumes.get(symbol) or 0)

        if action in BUY_ACTIONS:
            result = AShareConstraints.validate_buy_order(
                symbol=symbol,
                quantity=quantity,
                price=price,
                prev_close=prev_close,
                available_cash=1_000_000_000.0,
            )
        elif action in SELL_ACTIONS:
            result = AShareConstraints.validate_sell_order(
                symbol=symbol,
                quantity=quantity,
                price=price,
                prev_close=prev_close,
                available_shares=quantity,
            )
        else:
            result = _hold_result(quantity)

        capacity_ratio = quantity / volume if volume > 0 else None
        warnings = list(result.warnings)
        if capacity_ratio is not None and capacity_ratio > 0.1:
            warnings.append(
                f"订单数量占当日成交量 {capacity_ratio:.2%}，超过 10% 容量阈值",
            )

        rows.append(
            {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "date": run_date,
                "symbol": symbol,
                "action": action,
                "approved": bool(result.approved and (capacity_ratio is None or capacity_ratio <= 0.1)),
                "adjusted_quantity": int(result.adjusted_quantity or 0),
                "rejection_reason": result.rejection_reason,
                "warnings": warnings,
                "transaction_cost": float(result.transaction_cost or 0.0),
                "capacity_ratio": capacity_ratio,
                "slippage_estimate": _slippage_estimate(quantity, volume),
            },
        )
    return rows


def _extract_quantity(decision: dict[str, Any]) -> int:
    votes = decision.get("agent_votes")
    candidates = [
        decision.get("quantity"),
        decision.get("shares"),
        decision.get("target_quantity"),
    ]
    if isinstance(votes, dict):
        candidates.extend([votes.get("quantity"), votes.get("shares")])
    for value in candidates:
        try:
            quantity = int(float(value))
        except (TypeError, ValueError):
            continue
        if quantity > 0:
            return quantity
    return 100


def _hold_result(quantity: int):
    class Result:
        approved = True
        adjusted_quantity = quantity
        rejection_reason = ""
        warnings: list[str] = []
        transaction_cost = 0.0

    return Result()


def _slippage_estimate(quantity: int, volume: int) -> float | None:
    if volume <= 0:
        return None
    participation = quantity / volume
    return round(0.0005 + min(participation, 0.2) * 0.02, 6)
