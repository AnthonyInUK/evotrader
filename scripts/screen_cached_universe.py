#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Screen the locally cached A-share universe without LLM calls.

Pipeline:
  1. Discover tickers with local OHLCV parquet caches.
  2. Apply right-side technical structure filters.
  3. Add cached fundamental state when available.
  4. Add hot-sector/theme membership from cached sector index strength.

This script is intentionally diagnostic: it prints the count after each gate so
we can see whether the framework is too strict before wiring it into PM logic.
"""
from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE_DIR = ROOT / "backend" / "data" / "akshare_cache"
FINANCIAL_CACHE_DIR = ROOT / "backend" / "data" / "akshare_financial_cache"
OUTPUT_DIR = ROOT / "outputs" / "stock_selection"

_CACHE_RE = re.compile(
    r"^(?P<code>\d{6})_(?P<suffix>SH|SZ)_(?P<start>\d{4}-\d{2}-\d{2})_(?P<end>\d{4}-\d{2}-\d{2})$",
)


def _safe_symbol(ticker: str) -> str:
    return ticker.replace(".", "_")


def _ticker_from_match(match: re.Match[str]) -> str:
    return f"{match.group('code')}.{match.group('suffix')}"


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


def discover_price_caches(as_of_date: str) -> dict[str, Path]:
    """Return one best cache path per ticker that covers as_of_date."""
    as_of = pd.Timestamp(as_of_date)
    best: dict[str, tuple[pd.Timestamp, pd.Timestamp, Path]] = {}
    for path in CACHE_DIR.glob("*.parquet"):
        match = _CACHE_RE.match(path.stem)
        if not match:
            continue
        start = pd.Timestamp(match.group("start"))
        end = pd.Timestamp(match.group("end"))
        if not (start <= as_of <= end):
            continue
        ticker = _ticker_from_match(match)
        old = best.get(ticker)
        if old is None or (end, start) > (old[1], old[0]):
            best[ticker] = (start, end, path)
    return {ticker: item[2] for ticker, item in best.items()}


def _normalize_price_frame(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        if "time" in frame.columns:
            frame.index = pd.to_datetime(frame["time"], errors="coerce")
        else:
            frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[frame.index.notna()].sort_index()
    frame = frame.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        },
    )
    for col in ["open", "high", "low", "close", "volume"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna(subset=["close"])


def load_price_window(path: Path, as_of_date: str, lookback_days: int) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(path)
    except Exception:
        return None
    df = _normalize_price_frame(df)
    hist = df.loc[df.index <= pd.Timestamp(as_of_date)].tail(lookback_days).copy()
    if len(hist) < 80:
        return None
    return hist


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    close = frame["close"]
    frame["SMA5"] = close.rolling(5, min_periods=5).mean()
    frame["SMA20"] = close.rolling(20, min_periods=20).mean()
    frame["SMA60"] = close.rolling(60, min_periods=60).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    frame["MACD"] = ema12 - ema26
    frame["MACD_signal"] = frame["MACD"].ewm(span=9, adjust=False).mean()
    return frame


def technical_state(df: pd.DataFrame) -> dict[str, Any]:
    frame = add_indicators(df)
    latest = frame.iloc[-1]
    prev5 = frame.iloc[-6] if len(frame) >= 6 else frame.iloc[0]
    close = float(latest["close"])
    high = float(latest.get("high", close))
    sma5 = float(latest["SMA5"])
    sma20 = float(latest["SMA20"])
    sma60 = float(latest["SMA60"])
    macd = float(latest["MACD"])
    macd_signal = float(latest["MACD_signal"])
    ret20 = close / float(frame["close"].iloc[-21]) - 1 if len(frame) >= 21 else 0.0
    ret60 = close / float(frame["close"].iloc[-61]) - 1 if len(frame) >= 61 else 0.0
    sma20_slope_5d = sma20 / float(prev5["SMA20"]) - 1 if float(prev5["SMA20"]) else 0.0
    dist20 = close / sma20 - 1 if sma20 else 0.0

    if close > sma20 > sma60 and sma20_slope_5d > 0 and ret20 > 0 and macd >= macd_signal:
        trend = "RIGHT_SIDE_BULL"
    elif close >= sma60 and abs(dist20) <= 0.04 and macd >= macd_signal * 0.8:
        trend = "RIGHT_SIDE_PULLBACK"
    elif close < sma20 and macd < macd_signal and (close < sma60 or sma20_slope_5d < 0):
        trend = "NOT_RIGHT_SIDE"
    else:
        trend = "MIXED"

    if trend in {"RIGHT_SIDE_BULL", "RIGHT_SIDE_PULLBACK"} and -0.015 <= dist20 <= 0.03:
        entry = "PULLBACK_TO_TREND"
    elif trend == "RIGHT_SIDE_BULL" and dist20 > 0.06:
        entry = "EXTENDED_WAIT_PULLBACK"
    else:
        entry = "NO_ENTRY"

    exit_signal = "NONE"
    if close < sma5:
        exit_signal = "SMA5_DEFENSE_SELL"
    elif len(frame) >= 4:
        recent = frame.tail(4)
        prior_high = float(recent["high"].iloc[:-1].max()) if "high" in recent else high
        recent_close_high = float(recent["close"].max())
        ret3 = float(recent["close"].iloc[-1]) / float(recent["close"].iloc[0]) - 1
        if high <= prior_high and close < recent_close_high and ret3 <= 0:
            exit_signal = "THREE_DAY_NO_NEW_HIGH"

    return {
        "close": round(close, 3),
        "sma5": round(sma5, 3),
        "sma20": round(sma20, 3),
        "sma60": round(sma60, 3),
        "dist_to_sma20_pct": round(dist20 * 100, 2),
        "ret20_pct": round(ret20 * 100, 2),
        "ret60_pct": round(ret60 * 100, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_signal, 4),
        "trend_state": trend,
        "entry_signal": entry,
        "exit_signal": exit_signal,
    }


def _latest_financial_row(ticker: str, as_of_date: str, report_lag_days: int):
    path = FINANCIAL_CACHE_DIR / f"{_safe_symbol(ticker)}_financial_abstract.pkl"
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            df = pickle.load(fh)
    except Exception:
        return None
    if df is None or df.empty or "报告期" not in df.columns:
        return None
    frame = df.copy()
    frame["报告期"] = pd.to_datetime(frame["报告期"], errors="coerce")
    available_date = pd.Timestamp(as_of_date) - pd.Timedelta(days=report_lag_days)
    frame = frame.loc[frame["报告期"] <= available_date].dropna(subset=["报告期"])
    if frame.empty:
        return None
    return frame.sort_values("报告期").iloc[-1]


def fundamental_state(ticker: str, as_of_date: str, report_lag_days: int) -> tuple[str, dict[str, Any]]:
    row = _latest_financial_row(ticker, as_of_date, report_lag_days)
    if row is None:
        return "FUNDAMENTAL_UNKNOWN", {"reason": "NO_FINANCIAL_CACHE"}

    revenue_growth = _parse_number(row.get("营业总收入同比增长率"))
    profit_growth = _parse_number(row.get("净利润同比增长率"))
    roe = _parse_number(row.get("净资产收益率"))
    ocf_per_share = _parse_number(row.get("每股经营现金流"))
    debt_ratio = _parse_number(row.get("资产负债率"))

    is_bank = ticker.startswith("601398")
    is_financial = ticker.startswith(("601398", "601318", "600030"))
    hard_blocks: list[str] = []
    weak_flags: list[str] = []

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
    if not is_financial and debt_ratio is not None and debt_ratio > 85:
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


def load_hot_sector_members(as_of_date: str, lookback_days: int, top_n: int) -> tuple[list[dict], dict[str, list[str]]]:
    """Return hot sectors and ticker -> sector names mapping."""
    try:
        from backend.tools.sector_tools import get_sector_constituents, rank_hot_sectors
    except Exception:
        return [], {}

    hot_sectors = rank_hot_sectors(as_of_date=as_of_date, lookback_days=lookback_days, top_n=top_n)
    ticker_to_sectors: dict[str, list[str]] = {}
    for sector in hot_sectors:
        name = sector["name"]
        try:
            constituents = get_sector_constituents(name)
        except Exception:
            constituents = []
        for ticker in constituents:
            ticker_to_sectors.setdefault(ticker, []).append(name)
    return hot_sectors, ticker_to_sectors


def bucket_row(row: dict[str, Any], *, require_financial: bool) -> tuple[str, str]:
    if row["exit_signal"] != "NONE":
        return "WATCHLIST", "触发卖点/防守信号，不作为新买候选。"
    if row["trend_state"] == "NOT_RIGHT_SIDE":
        return "AVOID", "技术结构不是右侧。"
    if row["fundamental_state"] == "FUNDAMENTAL_BLOCK":
        return "AVOID", "财务硬伤过滤。"
    if require_financial and row["fundamental_state"] == "FUNDAMENTAL_UNKNOWN":
        return "WATCHLIST", "缺财务缓存，严格模式下不进入候选。"
    if row["theme_state"] == "THEME_COLD":
        return "WATCHLIST", "行业/主题不在热点里。"
    if row["trend_state"] == "RIGHT_SIDE_BULL" and row["theme_state"] in {"THEME_HOT", "THEME_UNKNOWN"}:
        if row["fundamental_state"] in {"FUNDAMENTAL_OK", "FUNDAMENTAL_UNKNOWN"}:
            if row["theme_state"] == "THEME_HOT":
                return "CORE_CANDIDATE", "右侧多头 + 热门行业/主题。"
            return "TECHNICAL_CANDIDATE", "右侧多头，但行业映射缺失，等待主题确认。"
        return "TACTICAL_CANDIDATE", "右侧多头，但财务有弱项。"
    if row["trend_state"] == "RIGHT_SIDE_PULLBACK" and row["theme_state"] == "THEME_HOT":
        return "ENTRY_SETUP_PULLBACK", "热门行业内右侧回踩趋势线。"
    return "WATCHLIST", "保留观察，等待趋势/主题/买点进一步确认。"


def screen(args: argparse.Namespace) -> dict[str, Any]:
    cache_paths = discover_price_caches(args.as_of)
    hot_sectors, ticker_to_hot_sectors = load_hot_sector_members(
        args.as_of,
        args.theme_lookback_days,
        args.top_sectors,
    )
    theme_mapping_available = bool(ticker_to_hot_sectors)
    rows: list[dict[str, Any]] = []
    skipped_price = 0

    for ticker, path in sorted(cache_paths.items()):
        df = load_price_window(path, args.as_of, args.lookback_days)
        if df is None:
            skipped_price += 1
            continue
        tech = technical_state(df)
        fundamental, details = fundamental_state(ticker, args.as_of, args.report_lag_days)
        hot_memberships = ticker_to_hot_sectors.get(ticker, [])
        if hot_memberships:
            theme_state = "THEME_HOT"
        elif theme_mapping_available:
            theme_state = "THEME_COLD"
        else:
            theme_state = "THEME_UNKNOWN"
        row = {
            "ticker": ticker,
            "date": args.as_of,
            "price_cache": path.name,
            **tech,
            "fundamental_state": fundamental,
            "fundamental_details": details,
            "theme_state": theme_state,
            "hot_sectors": hot_memberships,
        }
        bucket, reason = bucket_row(row, require_financial=args.require_financial)
        row["bucket"] = bucket
        row["selection_reason"] = reason
        rows.append(row)

    rows.sort(
        key=lambda item: (
            item["bucket"] not in {"CORE_CANDIDATE", "ENTRY_SETUP_PULLBACK", "TACTICAL_CANDIDATE"},
            item["exit_signal"] != "NONE",
            -float(item.get("ret60_pct") or 0),
            abs(float(item.get("dist_to_sma20_pct") or 999)),
        ),
    )

    summary = {
        "as_of": args.as_of,
        "price_cache_tickers": len(cache_paths),
        "price_insufficient": skipped_price,
        "analyzed": len(rows),
        "hot_sectors": hot_sectors,
        "theme_mapping_available": theme_mapping_available,
        "counts": {
            "trend_state": pd.Series([r["trend_state"] for r in rows]).value_counts().to_dict(),
            "fundamental_state": pd.Series([r["fundamental_state"] for r in rows]).value_counts().to_dict(),
            "theme_state": pd.Series([r["theme_state"] for r in rows]).value_counts().to_dict(),
            "bucket": pd.Series([r["bucket"] for r in rows]).value_counts().to_dict(),
            "entry_signal": pd.Series([r["entry_signal"] for r in rows]).value_counts().to_dict(),
            "exit_signal": pd.Series([r["exit_signal"] for r in rows]).value_counts().to_dict(),
        },
        "rows": rows,
    }
    return summary


def _write_outputs(result: dict[str, Any], json_out: str | None, csv_out: str | None) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_key = result["as_of"].replace("-", "")
    json_path = Path(json_out) if json_out else OUTPUT_DIR / f"cached_universe_screen_{date_key}.json"
    csv_path = Path(csv_out) if csv_out else OUTPUT_DIR / f"cached_universe_screen_{date_key}.csv"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    flat_rows = []
    for row in result["rows"]:
        item = row.copy()
        item["hot_sectors"] = ",".join(item.get("hot_sectors") or [])
        item["fundamental_details"] = json.dumps(item.get("fundamental_details"), ensure_ascii=False)
        flat_rows.append(item)
    pd.DataFrame(flat_rows).to_csv(csv_path, index=False)
    return json_path, csv_path


def print_report(result: dict[str, Any], max_rows: int) -> None:
    print("=== Cached Universe Screen ===")
    print(f"As of: {result['as_of']}")
    print(f"Price cache tickers: {result['price_cache_tickers']}")
    print(f"Analyzed: {result['analyzed']}  Price insufficient: {result['price_insufficient']}")
    print("\nHot sectors:")
    for sector in result["hot_sectors"]:
        print(f"  {sector['name']}: {sector['return_pct']:+.2f}% ({sector['data_days']}d)")
    print("\nCounts:")
    for key, values in result["counts"].items():
        print(f"  {key}: {values}")
    print(f"\nTop {max_rows}:")
    for row in result["rows"][:max_rows]:
        print(
            f"  {row['ticker']:<10} {row['bucket']:<20} "
            f"trend={row['trend_state']:<20} fund={row['fundamental_state']:<20} "
            f"theme={row['theme_state']:<10} sectors={','.join(row.get('hot_sectors') or []) or '-'} "
            f"close={row['close']:<8} ret60={row['ret60_pct']:+6.2f}% "
            f"dist20={row['dist_to_sma20_pct']:+6.2f}% entry={row['entry_signal']} exit={row['exit_signal']}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Screen locally cached A-share universe.")
    parser.add_argument("--as-of", default="2026-06-11", help="Analysis date, YYYY-MM-DD.")
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--theme-lookback-days", type=int, default=120)
    parser.add_argument("--top-sectors", type=int, default=8)
    parser.add_argument("--report-lag-days", type=int, default=90)
    parser.add_argument("--require-financial", action="store_true")
    parser.add_argument("--max-rows", type=int, default=50)
    parser.add_argument("--json-out")
    parser.add_argument("--csv-out")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = screen(args)
    json_path, csv_path = _write_outputs(result, args.json_out, args.csv_out)
    print_report(result, args.max_rows)
    print(f"\nJSON -> {json_path}")
    print(f"CSV  -> {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
