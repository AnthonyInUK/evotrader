from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from backend.data.historical_price_manager import HistoricalPriceManager


def build_selection_attribution(
    *,
    run_id: int,
    strategy_id: str,
    run_date: date,
    selection_rows: list[dict[str, Any]],
    benchmark_symbol: str | None = None,
) -> list[dict[str, Any]]:
    symbols = [str(row.get("ticker") or row.get("symbol")) for row in selection_rows]
    symbols = [symbol for symbol in symbols if symbol]
    if benchmark_symbol:
        symbols.append(benchmark_symbol)
    if not symbols:
        return []

    price_mgr = HistoricalPriceManager()
    price_mgr.subscribe(sorted(set(symbols)))
    price_mgr.preload_data(
        (run_date - timedelta(days=30)).isoformat(),
        (run_date + timedelta(days=25)).isoformat(),
    )

    benchmark_5d = (
        _forward_return(price_mgr, benchmark_symbol, run_date, 5)
        if benchmark_symbol
        else None
    )
    rows: list[dict[str, Any]] = []
    for rank, row in enumerate(selection_rows, start=1):
        symbol = str(row.get("ticker") or row.get("symbol"))
        fwd_1d = _forward_return(price_mgr, symbol, run_date, 1)
        fwd_5d = _forward_return(price_mgr, symbol, run_date, 5)
        fwd_10d = _forward_return(price_mgr, symbol, run_date, 10)
        rows.append(
            {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "date": run_date,
                "symbol": symbol,
                "rank": rank,
                "bucket": str(row.get("bucket") or "UNKNOWN"),
                "score": float(row.get("score") or row.get("selection_score") or 0.0),
                "forward_return_1d": fwd_1d,
                "forward_return_5d": fwd_5d,
                "forward_return_10d": fwd_10d,
                "benchmark_return_5d": benchmark_5d,
                "excess_return_5d": (
                    fwd_5d - benchmark_5d
                    if fwd_5d is not None and benchmark_5d is not None
                    else None
                ),
            },
        )
    return rows


def summarize_selection_attribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid_5d = [
        float(row["forward_return_5d"])
        for row in rows
        if row.get("forward_return_5d") is not None
    ]
    excess_5d = [
        float(row["excess_return_5d"])
        for row in rows
        if row.get("excess_return_5d") is not None
    ]
    return {
        "count": len(rows),
        "coverage_5d": len(valid_5d),
        "avg_forward_return_5d": _mean(valid_5d),
        "win_rate_5d": (
            sum(1 for value in valid_5d if value > 0) / len(valid_5d)
            if valid_5d
            else 0.0
        ),
        "avg_excess_return_5d": _mean(excess_5d),
    }


def _forward_return(
    price_mgr: HistoricalPriceManager,
    symbol: str | None,
    run_date: date,
    horizon_days: int,
) -> float | None:
    if not symbol:
        return None
    df = price_mgr._price_cache.get(symbol)
    if df is None or df.empty:
        return None
    frame = df.copy()
    if "date" in frame.columns:
        frame["_date"] = frame["date"].astype(str)
    elif "time" in frame.columns:
        frame["_date"] = frame["time"].astype(str)
    else:
        frame["_date"] = frame.index.astype(str)
    frame = frame.sort_values("_date").reset_index(drop=True)
    matches = frame.index[frame["_date"].str[:10] == run_date.isoformat()].tolist()
    if not matches:
        return None
    start_idx = matches[-1]
    end_idx = start_idx + horizon_days
    if end_idx >= len(frame):
        return None
    start = float(frame.loc[start_idx, "close"])
    end = float(frame.loc[end_idx, "close"])
    if start <= 0:
        return None
    return round(end / start - 1, 6)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 6) if values else 0.0
