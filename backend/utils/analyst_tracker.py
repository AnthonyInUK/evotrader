# -*- coding: utf-8 -*-
"""
Analyst Performance Tracker
Tracks analyst predictions and calculates win rates for leaderboard
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_SCORE_WEIGHTS = {
    "win_rate": 0.30,
    "rm": 0.175,
    "grounding": 0.175,
    "audit": 0.175,
    "presentation": 0.175,
}


def _get_score_weights() -> Dict[str, float]:
    """Get configurable leaderboard weights from env."""
    weights = DEFAULT_SCORE_WEIGHTS.copy()
    env_map = {
        "win_rate": "LEADERBOARD_WEIGHT_WIN_RATE",
        "rm": "LEADERBOARD_WEIGHT_RM",
        "grounding": "LEADERBOARD_WEIGHT_GROUNDING",
        "audit": "LEADERBOARD_WEIGHT_AUDIT",
        "presentation": "LEADERBOARD_WEIGHT_PRESENTATION",
    }
    for key, env_name in env_map.items():
        raw = os.getenv(env_name)
        if not raw:
            continue
        try:
            weights[key] = float(raw)
        except ValueError:
            logger.warning("Invalid leaderboard weight for %s=%s", env_name, raw)

    total = sum(weights.values())
    if total <= 0:
        return DEFAULT_SCORE_WEIGHTS.copy()
    return {k: v / total for k, v in weights.items()}


class AnalystPerformanceTracker:
    """
    Tracks analyst predictions and evaluates accuracy

    Workflow:
    1. Record analyst predictions for each ticker before market close
    2. After market close, evaluate predictions against actual returns
    3. Update leaderboard with win rates and statistics
    """

    def __init__(self):
        self.daily_predictions = {}
        self.daily_analysis_content = {}

    def record_analyst_predictions(
        self,
        final_predictions: List[Dict[str, Any]],
    ):
        """
        Record predictions from analysts for the current trading day

        Args:
            final_predictions: List of structured prediction results
                Format: [
                    {
                        'agent': 'analyst_name',
                        'predictions': [
                            {'ticker': 'AAPL', '
                            direction': 'up',
                            'confidence': 0.75},
                            ...
                        ]
                    },
                    ...
                ]
            tickers: List of tickers being analyzed
        """
        self.daily_predictions = {}
        self.daily_analysis_content = {}

        direction_mapping = {
            "up": "long",
            "down": "short",
            "neutral": "hold",
        }

        for result in final_predictions:
            analyst_id = result.get("agent")
            if not analyst_id:
                continue

            predictions = result.get("predictions", [])
            self.daily_analysis_content[analyst_id] = result.get(
                "raw_content",
                result.get("content", ""),
            ) or ""

            self.daily_predictions[analyst_id] = {}

            for pred in predictions:
                ticker = pred.get("ticker")
                direction = pred.get("direction", "neutral")

                if ticker:
                    signal = direction_mapping.get(direction, "hold")
                    self.daily_predictions[analyst_id][ticker] = signal

    def _score_analysis_sufficiency(self, text: str) -> float:
        """Heuristic completeness score (0-1)."""
        if not text:
            return 0.0
        length_score = min(len(text) / 1200, 1.0) * 0.35
        keyword_hits = sum(
            1
            for kw in [
                "reason", "理由", "because", "风险", "risk", "conclusion",
                "结论", "confidence", "信心", "signal", "催化", "catalyst",
            ]
            if kw.lower() in text.lower()
        )
        structure_hits = sum(
            1
            for kw in ["###", "-", "1.", "2.", "Summary", "Key", "Observ"]
            if kw in text
        )
        return round(
            min(0.4 + keyword_hits * 0.05 + structure_hits * 0.04 + length_score, 1.0),
            4,
        )

    def _score_grounding(self, text: str) -> float:
        """Rule-based grounding score from explicit evidence markers."""
        if not text:
            return 0.0
        score = 0.15
        if "[News Evidence]" in text:
            score += 0.25
        if re.search(r"\b\d{4}-\d{2}-\d{2}\b", text):
            score += 0.15
        if re.search(r"\b\d+(\.\d+)?%|\b\d+\.\d+\b", text):
            score += 0.2
        if "Source:" in text or "source=" in text:
            score += 0.15
        if any(kw in text for kw in ["Revenue Growth", "ROE", "MACD", "RSI", "Current Ratio"]):
            score += 0.15
        return round(min(score, 1.0), 4)

    def _score_audit(self, text: str, grounding_score: float) -> float:
        """
        Heuristic audit score: whether claims appear to stay close to evidence.
        Higher when the write-up references evidence and avoids over-assertive tone.
        """
        if not text:
            return 0.0
        score = 0.25 + grounding_score * 0.45
        if any(kw in text.lower() for kw in ["no recent news", "neutral", "lack of", "absence of"]):
            score += 0.15
        if any(kw in text.lower() for kw in ["guarantee", "certain", "definitely", "must rise"]):
            score -= 0.15
        return round(min(max(score, 0.0), 1.0), 4)

    def _score_presentation(self, text: str) -> float:
        """Readability / presentation quality score (0-1)."""
        if not text:
            return 0.0
        score = 0.2
        if any(token in text for token in ["###", "**", "-", "1.", "2."]):
            score += 0.25
        if "confidence" in text.lower():
            score += 0.15
        if any(kw in text.lower() for kw in ["signal", "bullish", "bearish", "neutral"]):
            score += 0.15
        if any(kw in text.lower() for kw in ["recommend", "conclusion", "summary", "建议", "结论"]):
            score += 0.15
        if 150 <= len(text) <= 2500:
            score += 0.1
        return round(min(score, 1.0), 4)

    def _build_quality_scores(self, analyst_id: str) -> Dict[str, float]:
        """Build 4-dimension quality scores for one analyst response."""
        text = self.daily_analysis_content.get(analyst_id, "")
        rm = self._score_analysis_sufficiency(text)
        grounding = self._score_grounding(text)
        audit = self._score_audit(text, grounding)
        presentation = self._score_presentation(text)
        overall = round((rm + grounding + audit + presentation) / 4, 4)
        return {
            "rm": rm,
            "grounding": grounding,
            "audit": audit,
            "presentation": presentation,
            "overall": overall,
        }

    def _build_score_snapshot(
        self,
        analyst_id: str,
        win_rate: Optional[float],
        date: str,
    ) -> Dict[str, Any]:
        """Build a dated 5-dimension score snapshot for leaderboard trends."""
        quality = self._build_quality_scores(analyst_id)
        weights = _get_score_weights()
        normalized_win_rate = win_rate if win_rate is not None else 0.0
        weighted = (
            normalized_win_rate * weights["win_rate"]
            + quality["rm"] * weights["rm"]
            + quality["grounding"] * weights["grounding"]
            + quality["audit"] * weights["audit"]
            + quality["presentation"] * weights["presentation"]
        )
        return {
            "date": date,
            "win_rate": win_rate,
            "rm": quality["rm"],
            "grounding": quality["grounding"],
            "audit": quality["audit"],
            "presentation": quality["presentation"],
            "overall_quality": quality["overall"],
            "weighted_score": round(weighted, 4),
            "weights": weights,
        }

    def evaluate_predictions(
        self,
        open_prices: Optional[Dict[str, float]],
        close_prices: Dict[str, float],
        date: str,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Evaluate analyst predictions against actual market moves

        Args:
            open_prices: Opening prices for each ticker
            close_prices: Closing prices for each ticker
            date: Trading date string (YYYY-MM-DD)

        Returns:
            Dict mapping analyst_id to evaluation results
        """
        evaluation_results = {}

        # Map internal signal types to frontend display names
        signal_display_map = {
            "long": "bull",
            "short": "bear",
            "hold": "neutral",
        }

        # Pool average return for relative win-rate (beta-adjusted metric).
        # A bullish call is "correct" if the stock beat the pool average,
        # not just if it went up in absolute terms.
        valid_returns = [
            (close_prices[t] - open_prices[t]) / open_prices[t]
            for t in open_prices
            if open_prices.get(t, 0) > 0 and close_prices.get(t, 0) > 0
        ]
        pool_avg_return = sum(valid_returns) / len(valid_returns) if valid_returns else 0.0

        for analyst_id, predictions in self.daily_predictions.items():
            correct_long = 0
            correct_short = 0
            incorrect_long = 0
            incorrect_short = 0
            unknown_long = 0
            unknown_short = 0
            hold_count = 0

            # Individual signal records for frontend display
            individual_signals: List[Dict[str, Any]] = []

            for ticker, prediction in predictions.items():
                open_price = open_prices.get(ticker, 0)
                close_price = close_prices.get(ticker, 0)

                signal_type = signal_display_map.get(prediction, "neutral")

                # Cannot evaluate if prices are missing
                if open_price <= 0 or close_price <= 0:
                    if prediction == "long":
                        unknown_long += 1
                    elif prediction == "short":
                        unknown_short += 1

                    individual_signals.append(
                        {
                            "ticker": ticker,
                            "signal": signal_type,
                            "date": date,
                            "is_correct": "unknown",
                        },
                    )
                    continue

                actual_return = (close_price - open_price) / open_price
                # Relative return vs pool average (removes market beta)
                relative_return = actual_return - pool_avg_return

                if prediction == "long":
                    is_correct = relative_return > 0
                    if is_correct:
                        correct_long += 1
                    else:
                        incorrect_long += 1

                    individual_signals.append(
                        {
                            "ticker": ticker,
                            "signal": signal_type,
                            "date": date,
                            "is_correct": is_correct,
                            "actual_return": round(actual_return, 4),
                            "relative_return": round(relative_return, 4),
                        },
                    )

                elif prediction == "short":
                    is_correct = relative_return < 0
                    if is_correct:
                        correct_short += 1
                    else:
                        incorrect_short += 1

                    individual_signals.append(
                        {
                            "ticker": ticker,
                            "signal": signal_type,
                            "date": date,
                            "is_correct": is_correct,
                            "actual_return": round(actual_return, 4),
                            "relative_return": round(relative_return, 4),
                        },
                    )

                elif prediction == "hold":
                    hold_count += 1
                    individual_signals.append(
                        {
                            "ticker": ticker,
                            "signal": signal_type,
                            "date": date,
                            "is_correct": None,
                        },
                    )

            total_long = correct_long + incorrect_long + unknown_long
            total_short = correct_short + incorrect_short + unknown_short
            evaluated_long = correct_long + incorrect_long
            evaluated_short = correct_short + incorrect_short
            total_evaluated = evaluated_long + evaluated_short
            correct_predictions = correct_long + correct_short

            win_rate = (
                correct_predictions / total_evaluated
                if total_evaluated > 0
                else None
            )

            quality_scores = self._build_quality_scores(analyst_id)
            evaluation_results[analyst_id] = {
                "total_predictions": total_evaluated,
                "correct_predictions": correct_predictions,
                "win_rate": win_rate,
                "quality_scores": quality_scores,
                "score_snapshot": self._build_score_snapshot(
                    analyst_id=analyst_id,
                    win_rate=win_rate,
                    date=date,
                ),
                "bull": {
                    "n": total_long,
                    "win": correct_long,
                    "unknown": unknown_long,
                },
                "bear": {
                    "n": total_short,
                    "win": correct_short,
                    "unknown": unknown_short,
                },
                "hold": hold_count,
                "signals": individual_signals,
            }

        return evaluation_results

    def clear_daily_predictions(self):
        """Clear predictions after evaluation"""
        self.daily_predictions = {}
        self.daily_analysis_content = {}

    def _process_single_pm_decision(
        self,
        _ticker: str,
        decision: Dict,
        open_price: float,
        close_price: float,
        _date: str,
        pool_avg_return: float = 0.0,
    ) -> Tuple[str, Optional[bool], str]:
        """
        Process a single PM decision and evaluate correctness

        Returns:
            Tuple of (prediction, is_correct, signal_type)
        """
        action = decision.get("action", "hold")

        # Convert action to prediction format
        if action in ["buy", "long"]:
            prediction = "long"
        elif action in ["sell", "short"]:
            prediction = "short"
        else:
            prediction = "hold"

        signal_display_map = {
            "long": "bull",
            "short": "bear",
            "hold": "neutral",
        }
        signal_type = signal_display_map.get(prediction, "neutral")

        # Handle invalid prices
        if open_price <= 0 or close_price <= 0:
            return prediction, None, signal_type

        # Use relative return vs pool average to remove market beta
        actual_return = (close_price - open_price) / open_price
        relative_return = actual_return - pool_avg_return

        if prediction == "long":
            is_correct = relative_return > 0
        elif prediction == "short":
            is_correct = relative_return < 0
        else:  # hold
            is_correct = None

        return prediction, is_correct, signal_type

    def evaluate_pm_decisions(
        self,
        pm_decisions: Dict[str, Dict],
        open_prices: Optional[Dict[str, float]],
        close_prices: Dict[str, float],
        date: str,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Evaluate PM's trading decisions against actual market moves

        Args:
            pm_decisions: PM decisions {ticker: {action, quantity, ...}}
            open_prices: Opening prices for each ticker
            close_prices: Closing prices for each ticker
            date: Trading date string (YYYY-MM-DD)

        Returns:
            Dict with 'portfolio_manager' key containing evaluation results
        """
        if not pm_decisions or not open_prices or not close_prices:
            return {}

        valid_returns = [
            (close_prices[t] - open_prices[t]) / open_prices[t]
            for t in open_prices
            if open_prices.get(t, 0) > 0 and close_prices.get(t, 0) > 0
        ]
        pool_avg_return = sum(valid_returns) / len(valid_returns) if valid_returns else 0.0

        correct_long = 0
        correct_short = 0
        incorrect_long = 0
        incorrect_short = 0
        unknown_long = 0
        unknown_short = 0
        hold_count = 0

        individual_signals: List[Dict[str, Any]] = []

        for ticker, decision in pm_decisions.items():
            open_price = open_prices.get(ticker, 0)
            close_price = close_prices.get(ticker, 0)

            (
                prediction,
                is_correct,
                signal_type,
            ) = self._process_single_pm_decision(
                ticker,
                decision,
                open_price,
                close_price,
                date,
                pool_avg_return=pool_avg_return,
            )

            if is_correct is None and (open_price <= 0 or close_price <= 0):
                if prediction == "long":
                    unknown_long += 1
                elif prediction == "short":
                    unknown_short += 1
                individual_signals.append(
                    {
                        "ticker": ticker,
                        "signal": signal_type,
                        "date": date,
                        "is_correct": "unknown",
                    },
                )
            elif prediction == "hold":
                hold_count += 1
                individual_signals.append(
                    {
                        "ticker": ticker,
                        "signal": signal_type,
                        "date": date,
                        "is_correct": None,
                    },
                )
            else:
                if prediction == "long":
                    if is_correct:
                        correct_long += 1
                    else:
                        incorrect_long += 1
                else:
                    if is_correct:
                        correct_short += 1
                    else:
                        incorrect_short += 1

                _op = open_prices.get(ticker, 0)
                _cl = close_prices.get(ticker, 0)
                _ar = (_cl - _op) / _op if _op > 0 else 0.0
                individual_signals.append(
                    {
                        "ticker": ticker,
                        "signal": signal_type,
                        "date": date,
                        "is_correct": is_correct,
                        "actual_return": round(_ar, 4),
                        "relative_return": round(_ar - pool_avg_return, 4),
                    },
                )

        total_long = correct_long + incorrect_long + unknown_long
        total_short = correct_short + incorrect_short + unknown_short
        evaluated_long = correct_long + incorrect_long
        evaluated_short = correct_short + incorrect_short
        total_evaluated = evaluated_long + evaluated_short
        correct_predictions = correct_long + correct_short

        win_rate = (
            correct_predictions / total_evaluated
            if total_evaluated > 0
            else None
        )

        return {
            "portfolio_manager": {
                "total_predictions": total_evaluated,
                "correct_predictions": correct_predictions,
                "win_rate": win_rate,
                "quality_scores": {
                    "rm": None,
                    "grounding": None,
                    "audit": None,
                    "presentation": None,
                    "overall": None,
                },
                "score_snapshot": None,
                "bull": {
                    "n": total_long,
                    "win": correct_long,
                    "unknown": unknown_long,
                },
                "bear": {
                    "n": total_short,
                    "win": correct_short,
                    "unknown": unknown_short,
                },
                "hold": hold_count,
                "signals": individual_signals,
            },
        }


def update_leaderboard_with_evaluations(
    leaderboard: List[Dict[str, Any]],
    evaluations: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Update leaderboard with new evaluation results

    Args:
        leaderboard: Current leaderboard data
        evaluations: Evaluation results for the day

    Returns:
        Updated leaderboard
    """
    for entry in leaderboard:
        agent_id = entry.get("agentId")
        if not agent_id or agent_id not in evaluations:
            continue

        eval_result = evaluations[agent_id]

        # Update aggregate stats
        entry["bull"]["n"] += eval_result["bull"]["n"]
        entry["bull"]["win"] += eval_result["bull"]["win"]
        entry["bull"]["unknown"] = (
            entry["bull"].get("unknown", 0) + eval_result["bull"]["unknown"]
        )
        entry["bear"]["n"] += eval_result["bear"]["n"]
        entry["bear"]["win"] += eval_result["bear"]["win"]
        entry["bear"]["unknown"] = (
            entry["bear"].get("unknown", 0) + eval_result["bear"]["unknown"]
        )

        # Calculate win rate based on evaluated signals only
        # evaluated = total - unknown
        evaluated_bull = entry["bull"]["n"] - entry["bull"]["unknown"]
        evaluated_bear = entry["bear"]["n"] - entry["bear"]["unknown"]
        total_evaluated = evaluated_bull + evaluated_bear
        total_wins = entry["bull"]["win"] + entry["bear"]["win"]

        if total_evaluated > 0:
            entry["winRate"] = round(total_wins / total_evaluated, 4)

        quality = eval_result.get("quality_scores") or {}
        if "qualityScores" not in entry:
            entry["qualityScores"] = {
                "rm": None,
                "grounding": None,
                "audit": None,
                "presentation": None,
                "overall": None,
                "count": 0,
            }

        current_count = entry["qualityScores"].get("count", 0) or 0
        if quality and quality.get("overall") is not None:
            new_count = current_count + 1
            for dim in ["rm", "grounding", "audit", "presentation", "overall"]:
                prev = entry["qualityScores"].get(dim)
                curr = quality.get(dim)
                if curr is None:
                    continue
                if prev is None or current_count == 0:
                    entry["qualityScores"][dim] = round(curr, 4)
                else:
                    entry["qualityScores"][dim] = round(
                        ((prev * current_count) + curr) / new_count,
                        4,
                    )
            entry["qualityScores"]["count"] = new_count

        if "qualityHistory" not in entry:
            entry["qualityHistory"] = []
        if quality:
            entry["qualityHistory"].append(quality)
            entry["qualityHistory"] = entry["qualityHistory"][-100:]

        if "performanceHistory" not in entry:
            entry["performanceHistory"] = []
        snapshot = eval_result.get("score_snapshot")
        if snapshot:
            entry["performanceHistory"].append(snapshot)
            entry["performanceHistory"] = entry["performanceHistory"][-180:]

        latest_snapshot = entry["performanceHistory"][-1] if entry["performanceHistory"] else None
        if latest_snapshot:
            entry["weightedScore"] = latest_snapshot.get("weighted_score")
            entry["scoreBreakdown"] = {
                "winRate": latest_snapshot.get("win_rate"),
                "rm": latest_snapshot.get("rm"),
                "grounding": latest_snapshot.get("grounding"),
                "audit": latest_snapshot.get("audit"),
                "presentation": latest_snapshot.get("presentation"),
                "overallQuality": latest_snapshot.get("overall_quality"),
            }

        # Add individual signal records
        if "signals" not in entry:
            entry["signals"] = []

        for signal in eval_result.get("signals", []):
            entry["signals"].append(signal)

        # Keep only recent signals (e.g., last 100 individual signals)
        entry["signals"] = entry["signals"][-100:]

    # Re-rank analysts by win rate (rank starts from 1)
    analyst_entries = [e for e in leaderboard if e.get("rank") is not None]
    analyst_entries.sort(
        key=lambda e: (
            e.get("weightedScore") if e.get("weightedScore") is not None else -1,
            e.get("winRate") if e.get("winRate") is not None else -1,
        ),
        reverse=True,
    )
    for idx, entry in enumerate(analyst_entries):
        entry["rank"] = idx + 1  # Rank 1 = highest win rate (gold medal)

    return leaderboard
