# -*- coding: utf-8 -*-
"""Offline stock selector replay for the right-side trend framework.

No LLM calls. Uses cached price data and simple theme/fundamental labels to
classify stocks before PM/entry-exit experiments.
"""
from __future__ import annotations

import argparse
import json
import pickle
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from backend.tools.analysis_tools import _add_basic_technical_indicators


THEME_MAP = {
    "600519.SH": ["白酒", "消费复苏", "核心资产"],
    "000858.SZ": ["白酒", "消费复苏"],
    "000333.SZ": ["家电", "消费复苏", "出口链"],
    "300750.SZ": ["新能源", "成长修复"],
    "600030.SH": ["券商", "活跃资本市场", "反弹beta"],
    "601318.SH": ["保险", "金融估值修复"],
    "601398.SH": ["银行", "高股息", "中特估"],
    "600276.SH": ["创新药", "医药修复"],
}

CACHE_DIR = Path(__file__).resolve().parents[1] / "backend" / "data" / "akshare_cache"
FINANCIAL_CACHE_DIR = (
    Path(__file__).resolve().parents[1] / "backend" / "data" / "akshare_financial_cache"
)

DEFAULT_HOT_THEMES = {
    "消费复苏",
    "活跃资本市场",
    "反弹beta",
    "金融估值修复",
    "高股息",
    "中特估",
    "成长修复",
    "医药修复",
}

MARKET_REGIMES = {"WEAK", "SIDEWAYS", "REBOUND"}


def _date_range(start: str, end: str) -> list[str]:
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    days = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _parse_cache_range(path: Path, safe_symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    prefix = f"{safe_symbol}_"
    if not path.stem.startswith(prefix):
        return None
    parts = path.stem[len(prefix) :].split("_")
    if len(parts) < 2:
        return None
    try:
        return pd.Timestamp(parts[-2]), pd.Timestamp(parts[-1])
    except Exception:
        return None


def _load_prices(ticker: str, end_date: str, lookback_days: int):
    """Load recent trading rows from a cache that actually covers end_date.

    The normal data tool has a permissive fallback for live backtests. For
    selector replay we want stricter behavior: if a cache only reaches January,
    it must not be used to answer a February selector question.
    """
    safe_symbol = ticker.replace(".", "_")
    requested_end = pd.Timestamp(end_date)
    candidates: list[tuple[int, pd.Timestamp, Path]] = []
    for path in CACHE_DIR.glob(f"{safe_symbol}_*.parquet"):
        cached_range = _parse_cache_range(path, safe_symbol)
        if cached_range is None:
            continue
        cached_start, cached_end = cached_range
        if cached_start <= requested_end <= cached_end:
            span = int((cached_end - cached_start).days)
            candidates.append((span, cached_start, path))

    for _, _, path in sorted(candidates, reverse=True):
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        if not isinstance(df.index, pd.DatetimeIndex):
            if "time" in df.columns:
                df = df.copy()
                df.index = pd.to_datetime(df["time"])
            else:
                df = df.copy()
                df.index = pd.to_datetime(df.index)
        df = df.sort_index()
        sliced = df.loc[df.index <= requested_end].tail(lookback_days).copy()
        if len(sliced) >= 30:
            return _add_basic_technical_indicators(sliced)

    if not candidates:
        return None
    return None


def _pct(value: float) -> float:
    return round(value * 100, 2)


def _parse_number(value: Any) -> float | None:
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text.lower() == "false":
        return None
    multiplier = 1.0
    if text.endswith("%"):
        text = text[:-1]
    if text.endswith("万"):
        multiplier = 1e4
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 1e8
        text = text[:-1]
    try:
        return float(text.replace(",", "")) * multiplier
    except ValueError:
        return None


def _safe_symbol(ticker: str) -> str:
    return ticker.replace(".", "_")


def _latest_financial_row(ticker: str, day: str, report_lag_days: int):
    path = FINANCIAL_CACHE_DIR / f"{_safe_symbol(ticker)}_financial_abstract.pkl"
    if not path.exists():
        return None
    try:
        df = pickle.load(open(path, "rb"))
    except Exception:
        return None
    if df is None or df.empty or "报告期" not in df.columns:
        return None
    df = df.copy()
    df["报告期"] = pd.to_datetime(df["报告期"], errors="coerce")
    available_date = pd.Timestamp(day) - pd.Timedelta(days=report_lag_days)
    df = df.loc[df["报告期"] <= available_date].dropna(subset=["报告期"])
    if df.empty:
        return None
    return df.sort_values("报告期").iloc[-1]


def _fundamental_state(ticker: str, day: str, report_lag_days: int) -> tuple[str, dict[str, Any]]:
    row = _latest_financial_row(ticker, day, report_lag_days)
    if row is None:
        return "FUNDAMENTAL_UNKNOWN", {"reason": "NO_FINANCIAL_CACHE"}

    revenue_growth = _parse_number(row.get("营业总收入同比增长率"))
    profit_growth = _parse_number(row.get("净利润同比增长率"))
    roe = _parse_number(row.get("净资产收益率"))
    ocf_per_share = _parse_number(row.get("每股经营现金流"))
    debt_ratio = _parse_number(row.get("资产负债率"))

    hard_blocks = []
    weak_flags = []
    is_bank = ticker in {"601398.SH"}
    is_financial = ticker in {"601398.SH", "601318.SH", "600030.SH"}

    if revenue_growth is not None and revenue_growth <= -15:
        hard_blocks.append("REVENUE_GROWTH_COLLAPSE")
    elif revenue_growth is not None and revenue_growth < 0 and not (
        is_bank and profit_growth is not None and profit_growth >= 0
    ):
        weak_flags.append("REVENUE_GROWTH_NEGATIVE")

    if profit_growth is not None and profit_growth <= -30:
        hard_blocks.append("PROFIT_GROWTH_COLLAPSE")
    elif profit_growth is not None and profit_growth < 0:
        weak_flags.append("PROFIT_GROWTH_NEGATIVE")

    roe_block_threshold = 4 if is_financial else 3
    roe_weak_threshold = 7 if is_financial else 8
    if roe is not None and roe < roe_block_threshold:
        hard_blocks.append("ROE_TOO_LOW")
    elif roe is not None and roe < roe_weak_threshold:
        weak_flags.append("ROE_WEAK")

    if not is_financial and ocf_per_share is not None and ocf_per_share < 0:
        weak_flags.append("OPERATING_CASH_FLOW_NEGATIVE")

    # 金融股天然高负债，第一版不因资产负债率单独 block。
    non_financial = not is_financial
    if non_financial and debt_ratio is not None and debt_ratio > 85:
        hard_blocks.append("DEBT_RATIO_TOO_HIGH")

    details = {
        "report_period": pd.Timestamp(row["报告期"]).strftime("%Y-%m-%d"),
        "revenue_growth_pct": revenue_growth,
        "profit_growth_pct": profit_growth,
        "roe_pct": roe,
        "ocf_per_share": ocf_per_share,
        "debt_ratio_pct": debt_ratio,
        "weak_flags": weak_flags,
        "hard_blocks": hard_blocks,
    }
    if hard_blocks:
        return "FUNDAMENTAL_BLOCK", details
    if weak_flags:
        return "FUNDAMENTAL_WEAK", details
    return "FUNDAMENTAL_OK", details


def _theme_state(ticker: str, hot_themes: set[str]) -> str:
    themes = set(THEME_MAP.get(ticker, []))
    if themes & hot_themes:
        return "THEME_HOT"
    if themes:
        return "THEME_NEUTRAL"
    return "THEME_COLD"


def _trend_state(df) -> str:
    latest = df.iloc[-1]
    prev5 = df.iloc[-6] if len(df) >= 6 else df.iloc[0]
    close = float(latest["close"])
    sma20 = float(latest["SMA20"])
    sma60 = float(latest["SMA60"])
    macd = float(latest["MACD"])
    signal = float(latest["MACD_signal"])
    ret10 = close / float(df["close"].iloc[-11]) - 1 if len(df) >= 11 else 0
    sma20_slope = sma20 / float(prev5["SMA20"]) - 1 if float(prev5["SMA20"]) else 0

    if close > sma20 and (close > sma60 or sma20 >= sma60) and macd >= signal and ret10 > 0:
        return "RIGHT_SIDE_BULL"
    if close >= sma60 and abs(close / sma20 - 1) <= 0.04 and macd >= signal * 0.8:
        return "RIGHT_SIDE_PULLBACK"
    if close < sma20 and macd < signal and (close < sma60 or sma20_slope < 0):
        return "NOT_RIGHT_SIDE"
    return "MIXED"


def _entry_signal(df, trend_state: str) -> str:
    latest = df.iloc[-1]
    close = float(latest["close"])
    sma20 = float(latest["SMA20"])
    sma60 = float(latest["SMA60"])
    macd = float(latest["MACD"])
    signal = float(latest["MACD_signal"])
    dist20 = close / sma20 - 1 if sma20 else 0

    if trend_state in {"RIGHT_SIDE_BULL", "RIGHT_SIDE_PULLBACK"}:
        if -0.015 <= dist20 <= 0.03 and close >= sma60 and macd >= signal * 0.9:
            return "PULLBACK_TO_TREND"
        if dist20 > 0.06:
            return "EXTENDED_WAIT_PULLBACK"
    return "NO_ENTRY"


def _selection_status(
    bucket: str,
    entry_signal: str,
    exit_signal: str,
    market_regime: str,
) -> tuple[str, str]:
    if exit_signal != "NONE":
        return (
            "BLOCKED_BY_EXIT_SIGNAL",
            "候选股已触发卖点/防守信号，选股层不把它列为新入场优先项。",
        )
    if bucket not in {"CORE_CANDIDATE", "TACTICAL_CANDIDATE"}:
        return (
            "WATCH_ONLY",
            "未进入核心/战术候选，只保留观察。",
        )
    if entry_signal == "PULLBACK_TO_TREND":
        return (
            "ENTRY_SETUP_PULLBACK",
            "右侧候选回踩趋势线，属于标准入场形态。",
        )
    if market_regime == "REBOUND":
        return (
            "REBOUND_RIGHT_SIDE_NO_PULLBACK",
            "反弹环境中的右侧候选，但尚未回踩趋势线，交给分析师/PM判断是否参与。",
        )
    return (
        "WAIT_FOR_PULLBACK",
        "候选成立但没有回踩买点。",
    )


def _exit_signal(df) -> str:
    if len(df) < 6:
        return "NONE"
    latest = df.iloc[-1]
    close = float(latest["close"])
    sma5 = float(df["close"].tail(5).mean())
    if close < sma5:
        return "SMA5_DEFENSE_SELL"

    recent = df.tail(4)
    prior_high = float(recent["high"].iloc[:-1].max())
    current_high = float(recent["high"].iloc[-1])
    recent_close_high = float(recent["close"].max())
    ret3 = float(recent["close"].iloc[-1]) / float(recent["close"].iloc[0]) - 1
    if current_high <= prior_high and close < recent_close_high and ret3 <= 0:
        return "THREE_DAY_NO_NEW_HIGH"
    return "NONE"


def _bucket(trend: str, fundamental: str, theme: str) -> str:
    if fundamental == "FUNDAMENTAL_BLOCK" or trend == "NOT_RIGHT_SIDE":
        return "AVOID"
    if (
        trend == "RIGHT_SIDE_BULL"
        and fundamental == "FUNDAMENTAL_OK"
        and theme in {"THEME_HOT", "THEME_NEUTRAL"}
    ):
        return "CORE_CANDIDATE"
    if trend == "RIGHT_SIDE_BULL" and theme == "THEME_HOT":
        return "TACTICAL_CANDIDATE"
    if trend in {"RIGHT_SIDE_PULLBACK", "MIXED"} and fundamental != "FUNDAMENTAL_BLOCK":
        return "WATCHLIST"
    return "WATCHLIST"


def _finalize_selection(row: dict[str, Any], market_regime: str) -> None:
    bucket = _bucket(
        row["trend_state"],
        row["fundamental_state"],
        row["theme_state"],
    )
    status, reason = _selection_status(
        bucket,
        row["entry_signal"],
        row["exit_signal"],
        market_regime,
    )
    row["bucket"] = bucket
    row["selection_status"] = status
    row["selection_reason"] = reason


def _apply_relative_theme_strength(rows: list[dict[str, Any]]) -> None:
    returns = [
        float(row["half_year_return_pct"])
        for row in rows
        if row.get("data_status") == "OK" and row.get("half_year_return_pct") is not None
    ]
    if not returns:
        for row in rows:
            row["theme_state"] = _theme_state(row["ticker"], DEFAULT_HOT_THEMES)
            row["theme_strength_pct"] = None
        return

    universe_median = float(pd.Series(returns).median())
    theme_values: dict[str, list[float]] = {}
    for row in rows:
        value = row.get("half_year_return_pct")
        if row.get("data_status") != "OK" or value is None:
            continue
        for theme in THEME_MAP.get(row["ticker"], []):
            theme_values.setdefault(theme, []).append(float(value))

    theme_strength = {
        theme: sum(values) / len(values) - universe_median
        for theme, values in theme_values.items()
        if values
    }
    ranked = sorted(theme_strength.items(), key=lambda item: item[1], reverse=True)
    top_themes = {theme for theme, _ in ranked[: max(1, min(3, len(ranked)))]}

    for row in rows:
        themes = THEME_MAP.get(row["ticker"], [])
        strengths = [theme_strength.get(theme) for theme in themes if theme in theme_strength]
        best_strength = max(strengths) if strengths else None
        row["theme_strength_pct"] = round(best_strength, 2) if best_strength is not None else None
        row["theme_strength_basis"] = "RELATIVE_AVAILABLE_RETURN"
        if best_strength is None:
            row["theme_state"] = "THEME_NEUTRAL" if themes else "THEME_COLD"
        elif best_strength >= 3 or any(theme in top_themes for theme in themes):
            row["theme_state"] = "THEME_HOT"
        elif best_strength <= -5:
            row["theme_state"] = "THEME_COLD"
        else:
            row["theme_state"] = "THEME_NEUTRAL"


def _apply_fixed_theme_tags(rows: list[dict[str, Any]], hot_themes: set[str]) -> None:
    for row in rows:
        row["theme_state"] = _theme_state(row["ticker"], hot_themes)
        row["theme_strength_pct"] = None
        row["theme_strength_basis"] = "FIXED_THEME_TAG"


def analyze_ticker(
    ticker: str,
    day: str,
    hot_themes: set[str],
    lookback_days: int,
    market_regime: str,
    report_lag_days: int,
) -> dict[str, Any]:
    df = _load_prices(ticker, day, lookback_days)
    if df is None or df.empty:
        return {
            "ticker": ticker,
            "date": day,
            "data_status": "INSUFFICIENT_DATA",
            "bucket": "WATCHLIST",
        }

    trend = _trend_state(df)
    fundamental, fundamental_details = _fundamental_state(ticker, day, report_lag_days)
    theme = _theme_state(ticker, hot_themes)
    entry = _entry_signal(df, trend)
    exit_sig = _exit_signal(df)
    latest = df.iloc[-1]
    close = float(latest["close"])
    sma20 = float(latest["SMA20"])
    sma60 = float(latest["SMA60"])
    dist20 = close / sma20 - 1 if sma20 else 0
    half_year_return = None
    if len(df) >= min(60, lookback_days):
        base_close = float(df["close"].iloc[0])
        if base_close:
            half_year_return = _pct(close / base_close - 1)

    row = {
        "ticker": ticker,
        "date": day,
        "data_status": "OK",
        "close": round(close, 2),
        "sma20": round(sma20, 2),
        "sma60": round(sma60, 2),
        "dist_to_sma20_pct": _pct(dist20),
        "macd": round(float(latest["MACD"]), 4),
        "macd_signal": round(float(latest["MACD_signal"]), 4),
        "half_year_return_pct": half_year_return,
        "return_lookback_trading_days": int(len(df)),
        "trend_state": trend,
        "fundamental_state": fundamental,
        "fundamental_details": fundamental_details,
        "theme_state": theme,
        "theme_strength_pct": None,
        "theme_strength_basis": "FIXED_THEME_TAG",
        "themes": THEME_MAP.get(ticker, []),
        "market_regime": market_regime,
        "entry_signal": entry,
        "exit_signal": exit_sig,
    }
    _finalize_selection(row, market_regime)
    return row


def _print_report(result: dict[str, Any]) -> None:
    print("=== Stock Selection Replay ===")
    print(f"Dates: {', '.join(result['dates'])}")
    print(f"Tickers: {', '.join(result['tickers'])}")
    print(f"Hot themes: {', '.join(sorted(result['hot_themes']))}")
    print(f"Market regime: {result['market_regime']}")
    print(f"Theme mode: {result['theme_mode']}")
    print(f"Report lag days: {result['report_lag_days']}")
    print()
    for day in result["days"]:
        print(f"## {day['date']}")
        for row in day["rows"]:
            if row.get("data_status") != "OK":
                print(f"  {row['ticker']}: DATA_GAP bucket={row['bucket']}")
                continue
            print(
                "  "
                f"{row['ticker']}: {row['bucket']} "
                f"trend={row['trend_state']} "
                f"fund={row['fundamental_state']} "
                f"theme={row['theme_state']} "
                f"themeStr={row.get('theme_strength_pct')} "
                f"entry={row['entry_signal']} "
                f"exit={row['exit_signal']} "
                f"status={row['selection_status']} "
                f"close={row['close']} "
                f"dist20={row['dist_to_sma20_pct']}%"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay stock selection framework without LLM.")
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--date", action="append")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--lookback-days", type=int, default=120)
    parser.add_argument(
        "--report-lag-days",
        type=int,
        default=90,
        help="Use only financial reports whose period end is at least this many days before the replay date.",
    )
    parser.add_argument(
        "--market-regime",
        choices=sorted(MARKET_REGIMES),
        default="SIDEWAYS",
        help="Fixed market regime for selector replay. Keep explicit to avoid mixing regime and entry-rule experiments.",
    )
    parser.add_argument(
        "--theme-mode",
        choices=["fixed", "relative"],
        default="relative",
        help="fixed uses manual hot theme tags; relative ranks themes by 120-trading-day relative strength inside the replay pool.",
    )
    parser.add_argument("--hot-themes", default=",".join(sorted(DEFAULT_HOT_THEMES)))
    parser.add_argument("--json-out")
    args = parser.parse_args()

    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    if args.date:
        dates = args.date
    elif args.start and args.end:
        dates = _date_range(args.start, args.end)
    else:
        raise SystemExit("Pass --date or --start/--end.")

    hot_themes = {item.strip() for item in args.hot_themes.split(",") if item.strip()}
    days = []
    for day in dates:
        rows = [
            analyze_ticker(
                ticker,
                day,
                hot_themes,
                args.lookback_days,
                args.market_regime,
                args.report_lag_days,
            )
            for ticker in tickers
        ]
        if args.theme_mode == "relative":
            _apply_relative_theme_strength(rows)
        else:
            _apply_fixed_theme_tags(rows, hot_themes)
        for row in rows:
            if row.get("data_status") == "OK":
                _finalize_selection(row, args.market_regime)
        days.append({"date": day, "rows": rows})

    result = {
        "tickers": tickers,
        "dates": dates,
        "market_regime": args.market_regime,
        "theme_mode": args.theme_mode,
        "report_lag_days": args.report_lag_days,
        "hot_themes": sorted(hot_themes),
        "days": days,
    }
    if args.json_out:
        path = Path(args.json_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _print_report(result)


if __name__ == "__main__":
    main()
