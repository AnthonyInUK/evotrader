#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Seed A-share company news snapshots for a ticker/date range.

Workflow:
1. Fetch the latest available A-share news feed once per ticker.
2. Re-slice the feed into multiple historical end-date snapshots.
3. Persist snapshots under backend/data/company_news_snapshots/.

Note:
- This does not magically recover arbitrary old news history.
- It only materializes snapshots from the coverage already present in the
  current upstream feed. If the requested date range is earlier than the
  earliest fetched article, the script will create empty snapshots and warn.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from backend.tools import data_tools  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed A-share news snapshots")
    parser.add_argument(
        "--tickers",
        nargs="+",
        required=True,
        help="A-share tickers, e.g. 600519.SH 300750.SZ",
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="Start date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="End date in YYYY-MM-DD",
    )
    parser.add_argument(
        "--step-days",
        type=int,
        default=7,
        help="Snapshot spacing in days, default 7",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max articles per snapshot, default 50",
    )
    return parser.parse_args()


def _date_range(start_date: str, end_date: str, step_days: int) -> list[str]:
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    anchors = []
    cursor = start
    while cursor <= end:
        anchors.append(cursor.strftime("%Y-%m-%d"))
        cursor += timedelta(days=max(step_days, 1))
    if not anchors or anchors[-1] != end.strftime("%Y-%m-%d"):
        anchors.append(end.strftime("%Y-%m-%d"))
    return anchors


def _seed_ticker(
    ticker: str,
    start_date: str,
    end_date: str,
    step_days: int,
    limit: int,
) -> None:
    live_news = data_tools._fetch_akshare_company_news(  # noqa: SLF001
        ticker=ticker,
        start_date=None,
        end_date=datetime.today().strftime("%Y-%m-%d"),
        limit=200,
    )
    if not live_news:
        print(f"[WARN] {ticker}: no live feed returned, skip")
        return

    article_dates = [
        item.date for item in live_news if item.date
    ]
    earliest = min(article_dates) if article_dates else "unknown"
    latest = max(article_dates) if article_dates else "unknown"
    print(
        f"[INFO] {ticker}: fetched {len(live_news)} live articles, "
        f"coverage={earliest}..{latest}"
    )

    for anchor in _date_range(start_date, end_date, step_days):
        sliced = data_tools._filter_company_news_by_date(  # noqa: SLF001
            live_news,
            start_date=start_date,
            end_date=anchor,
            limit=limit,
        )
        data_tools._save_company_news_snapshot(  # noqa: SLF001
            ticker=ticker,
            start_date=start_date,
            end_date=anchor,
            limit=limit,
            source="akshare",
            news=sliced,
        )
        print(
            f"[SEED] {ticker} @ {anchor}: {len(sliced)} articles -> "
            f"company_news_snapshots"
        )

    if earliest != "unknown" and end_date < earliest:
        print(
            f"[WARN] {ticker}: requested range ends before upstream feed coverage "
            f"({earliest}). Older anchors will remain empty until a richer "
            "historical news source is connected."
        )


def main() -> None:
    args = _parse_args()
    for ticker in args.tickers:
        _seed_ticker(
            ticker=ticker,
            start_date=args.start_date,
            end_date=args.end_date,
            step_days=args.step_days,
            limit=args.limit,
        )


if __name__ == "__main__":
    main()
