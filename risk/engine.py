from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


@dataclass
class RiskResult:
    passed: bool
    event_type: str
    detail: str
    triggered_at: datetime


class RiskEngine:
    def check_position_limit(
        self,
        symbol: str,
        weight: float,
        limit: float,
    ) -> RiskResult:
        passed = abs(weight) <= limit
        return RiskResult(
            passed=passed,
            event_type="position_limit",
            detail=(
                f"{symbol} weight {weight:.4f} within limit {limit:.4f}"
                if passed
                else f"{symbol} weight {weight:.4f} exceeds limit {limit:.4f}"
            ),
            triggered_at=datetime.now(timezone.utc),
        )

    def check_drawdown(
        self,
        current_drawdown: float,
        limit: float,
    ) -> RiskResult:
        passed = current_drawdown <= limit
        return RiskResult(
            passed=passed,
            event_type="drawdown_limit",
            detail=(
                f"drawdown {current_drawdown:.4f} within limit {limit:.4f}"
                if passed
                else f"drawdown {current_drawdown:.4f} exceeds limit {limit:.4f}"
            ),
            triggered_at=datetime.now(timezone.utc),
        )

    def check_correlation(
        self,
        signals: Iterable[dict],
        threshold: float,
    ) -> RiskResult:
        signal_list = list(signals)
        if len(signal_list) < 2:
            return RiskResult(
                passed=True,
                event_type="correlation_limit",
                detail="fewer than two signals; correlation check skipped",
                triggered_at=datetime.now(timezone.utc),
            )
        scores = [float(item.get("score", 0.0)) for item in signal_list]
        same_direction = sum(1 for score in scores if score >= 0) / len(scores)
        concentration = max(same_direction, 1 - same_direction)
        passed = concentration <= threshold
        return RiskResult(
            passed=passed,
            event_type="correlation_limit",
            detail=(
                f"direction concentration {concentration:.4f} within {threshold:.4f}"
                if passed
                else f"direction concentration {concentration:.4f} exceeds {threshold:.4f}"
            ),
            triggered_at=datetime.now(timezone.utc),
        )

    def check_concentration(
        self,
        portfolio: dict[str, Any],
        max_single: float,
    ) -> RiskResult:
        positions = portfolio.get("positions", {}) if portfolio else {}
        weights = []
        for symbol, position in positions.items():
            if isinstance(position, dict):
                weights.append((symbol, float(position.get("weight", 0.0))))
        if not weights:
            return RiskResult(
                passed=True,
                event_type="concentration_limit",
                detail="no weighted positions; concentration check skipped",
                triggered_at=datetime.now(timezone.utc),
            )
        symbol, weight = max(weights, key=lambda item: abs(item[1]))
        return self.check_position_limit(symbol, weight, max_single)

    def run_all_checks(
        self,
        portfolio: dict[str, Any],
        config: Any,
        signals: list[dict] | None = None,
        current_drawdown: float = 0.0,
    ) -> list[RiskResult]:
        limits = config.risk_limits
        results = [
            self.check_drawdown(current_drawdown, limits.max_drawdown),
            self.check_concentration(portfolio, limits.max_position_size),
            self.check_correlation(signals or [], limits.max_correlation),
        ]
        return results

    def sanitize_signals(
        self,
        signals: list[dict],
        risk_results: list[RiskResult],
    ) -> list[dict]:
        if all(result.passed for result in risk_results):
            return signals
        return [
            {
                **signal,
                "score": 0.0,
                "confidence": min(float(signal.get("confidence", 0.0)), 50.0),
                "rationale": (
                    f"{signal.get('rationale', '')} | sanitized by risk engine"
                ).strip(),
            }
            for signal in signals
        ]
