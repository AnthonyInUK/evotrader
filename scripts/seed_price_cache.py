#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Seed A-share historical price cache before running multi-stock experiments.

This script is intentionally separate from the backtest runner. Expanding the
stock pool should first prove that data exists, without spending LLM calls.
"""

from __future__ import annotations

import argparse
import signal
import sys
from contextlib import contextmanager
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from backend.data.historical_price_manager import (  # noqa: E402
    _load_akshare,
    _load_from_disk_cache,
    _save_to_disk_cache,
    _to_akshare_symbol,
)


class TimeoutError(Exception):
    """Raised when one ticker takes too long to seed."""


@contextmanager
def _timeout(seconds: int):
    def _handler(_signum, _frame):
        raise TimeoutError(f"timeout after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(max(seconds, 1))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed A-share price cache")
    parser.add_argument(
        "--tickers",
        nargs="+",
        required=True,
        help="A-share tickers, e.g. 600519.SH 300750.SZ",
    )
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=45,
        help="Per-ticker timeout, default 45 seconds",
    )
    parser.add_argument(
        "--source",
        choices=["auto", "sina"],
        default="auto",
        help="Data source. auto uses project loader; sina skips Eastmoney.",
    )
    return parser.parse_args()


def _load_sina(ticker: str, start_date: str, end_date: str) -> pd.DataFrame | None:
    import akshare as ak

    symbol = _to_akshare_symbol(ticker)
    sina_symbol = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"
    df = ak.stock_zh_a_daily(
        symbol=sina_symbol,
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="qfq",
    )
    if df is None or df.empty:
        return None

    df = df.rename(columns={"date": "time"})
    keep_cols = [
        c for c in ["time", "open", "close", "high", "low", "volume"] if c in df.columns
    ]
    df = df[keep_cols].copy()
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    df.sort_index(inplace=True)
    df["ret"] = df["close"].pct_change() * 100
    return df


def main() -> int:
    args = _parse_args()
    ok: list[str] = []
    failed: list[str] = []

    for ticker in args.tickers:
        cached = _load_from_disk_cache(
            ticker,
            args.start_date,
            args.end_date,
        )
        if cached is not None and not cached.empty:
            print(f"[CACHE] {ticker}: {len(cached)} rows")
            ok.append(ticker)
            continue

        try:
            with _timeout(args.timeout_seconds):
                if args.source == "sina":
                    df = _load_sina(ticker, args.start_date, args.end_date)
                else:
                    df = _load_akshare(ticker, args.start_date, args.end_date)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {ticker}: {exc}")
            failed.append(ticker)
            continue

        if df is None or df.empty:
            print(f"[MISS] {ticker}: no price rows")
            failed.append(ticker)
            continue

        _save_to_disk_cache(df, ticker, args.start_date, args.end_date)
        print(f"[SEEDED] {ticker}: {len(df)} rows")
        ok.append(ticker)

    print("\n=== Price Cache Seed Summary ===")
    print(f"OK     ({len(ok)}): {', '.join(ok) if ok else '-'}")
    print(f"FAILED ({len(failed)}): {', '.join(failed) if failed else '-'}")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
