from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Iterable

import numpy as np


def calc_sharpe(returns: Iterable[float], risk_free: float = 0.02) -> float:
    values = list(returns)
    if len(values) < 2:
        return 0.0
    daily_rf = risk_free / 252
    excess = [value - daily_rf for value in values]
    vol = pstdev(excess)
    if vol == 0:
        return 0.0
    return mean(excess) / vol * math.sqrt(252)


def calc_max_drawdown(equity_curve: Iterable[float]) -> float:
    peak = None
    max_drawdown = 0.0
    for value in equity_curve:
        peak = value if peak is None else max(peak, value)
        if peak:
            max_drawdown = max(max_drawdown, (peak - value) / peak)
    return max_drawdown


def calc_ic(predicted_scores: Iterable[float], actual_returns: Iterable[float]) -> float:
    scores = list(predicted_scores)
    returns = list(actual_returns)
    if len(scores) < 2 or len(scores) != len(returns):
        return 0.0
    corr = np.corrcoef(scores, returns)[0, 1]
    return 0.0 if np.isnan(corr) else float(corr)


def calc_ic_ir(ic_series: Iterable[float]) -> float:
    values = list(ic_series)
    if len(values) < 2:
        return 0.0
    std = pstdev(values)
    return 0.0 if std == 0 else mean(values) / std


def calc_win_rate(decisions: Iterable[dict]) -> float:
    rows = list(decisions)
    if not rows:
        return 0.0
    wins = 0
    counted = 0
    for row in rows:
        pnl = row.get("pnl")
        if pnl is None:
            continue
        counted += 1
        wins += 1 if float(pnl) > 0 else 0
    return wins / counted if counted else 0.0


async def calc_metrics_report(
    run_id: int,
    decisions: list[dict],
    backtest_repo=None,
    equity_curve: list[float] | None = None,
    returns: list[float] | None = None,
    predicted_scores: list[float] | None = None,
    actual_returns: list[float] | None = None,
) -> dict:
    metrics = {
        "run_id": run_id,
        "sharpe": calc_sharpe(returns or []),
        "max_drawdown": calc_max_drawdown(equity_curve or []),
        "win_rate": calc_win_rate(decisions),
        "IC": calc_ic(predicted_scores or [], actual_returns or []),
        "IC_IR": calc_ic_ir([]),
    }
    if backtest_repo is not None:
        await backtest_repo.update_metrics(run_id, metrics)
    return metrics
