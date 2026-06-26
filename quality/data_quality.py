from __future__ import annotations

from datetime import date
from typing import Any


def build_data_quality_report(
    *,
    strategy_id: str,
    run_date: date,
    expected_symbols: list[str],
    market: dict[str, Any],
) -> dict[str, Any]:
    rows = list(market.get("rows") or [])
    present = {row["symbol"] for row in rows}
    missing_symbols = [symbol for symbol in expected_symbols if symbol not in present]
    zero_volume = [
        row["symbol"]
        for row in rows
        if int(row.get("volume") or 0) <= 0
    ]
    missing_prev_close = [
        symbol
        for symbol in present
        if symbol not in (market.get("prev_closes") or {})
    ]
    suspicious_returns = []
    for row in rows:
        prev = float((market.get("prev_closes") or {}).get(row["symbol"]) or 0.0)
        close = float(row.get("close") or 0.0)
        if prev <= 0 or close <= 0:
            continue
        ret = close / prev - 1
        if abs(ret) > 0.25:
            suspicious_returns.append(
                {
                    "symbol": row["symbol"],
                    "prev_close": prev,
                    "close": close,
                    "return": round(ret, 6),
                },
            )

    report = {
        "strategy_id": strategy_id,
        "date": run_date.isoformat(),
        "expected_symbols": len(expected_symbols),
        "loaded_symbols": len(present),
        "price_coverage": round(len(present) / len(expected_symbols), 6)
        if expected_symbols
        else 1.0,
        "missing_symbols": missing_symbols,
        "zero_volume_symbols": zero_volume,
        "missing_prev_close_symbols": missing_prev_close,
        "suspicious_returns": suspicious_returns,
        "checks": {
            "price_coverage_ok": not missing_symbols,
            "volume_ok": not zero_volume,
            "prev_close_ok": not missing_prev_close,
            "return_range_ok": not suspicious_returns,
        },
    }
    report["passed"] = all(report["checks"].values())
    return report
