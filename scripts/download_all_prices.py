#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
全量 A 股历史价格批量下载脚本（baostock 数据源）

用法：
    cd evotraders
    python scripts/download_all_prices.py

    # 只下载沪市
    python scripts/download_all_prices.py --market sh

    # 从失败列表重试
    python scripts/download_all_prices.py --retry-failed

    # 自定义日期范围
    python scripts/download_all_prices.py --start 2023-01-01 --end 2026-06-11

特性：
    - 数据源：baostock（稳定、免登录 key、专为 A 股批量设计）
    - Ticker 列表：baostock query_stock_basic，全量 A 股
    - 并发 4 线程，每线程独立 baostock session
    - 断点续传：已缓存的自动跳过
    - 失败列表写入 scripts/download_failed.txt，可用 --retry-failed 重跑
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: F401 (保留备用)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util as _ilu

# 直接从文件加载，绕开 backend/data/__init__.py（后者 import finnhub 会失败）
_spec = _ilu.spec_from_file_location(
    "historical_price_manager",
    ROOT / "backend" / "data" / "historical_price_manager.py",
)
_hpm = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_hpm)

_load_from_disk_cache = _hpm._load_from_disk_cache
_save_to_disk_cache   = _hpm._save_to_disk_cache

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

START_DATE = "2023-01-01"
END_DATE   = "2026-06-11"
WORKERS    = 1   # baostock 用全局 socket，不支持多线程并发
FAILED_LOG = Path(__file__).parent / "download_failed.txt"


# ── ticker 格式转换 ──────────────────────────────────────────────────────────

def _bs_to_internal(bs_code: str) -> str | None:
    """
    baostock 格式 → EvoTraders 内部格式
    sh.600519 → 600519.SH
    sz.000001 → 000001.SZ
    """
    if "." not in bs_code:
        return None
    market, code = bs_code.split(".", 1)
    if market == "sh":
        return f"{code}.SH"
    if market == "sz":
        return f"{code}.SZ"
    return None


def _internal_to_bs(ticker: str) -> str:
    """
    EvoTraders 内部格式 → baostock 格式
    600519.SH → sh.600519
    000001.SZ → sz.000001
    """
    code   = ticker.split(".")[0]
    suffix = ticker.split(".")[-1].upper() if "." in ticker else ""
    if suffix == "SH":
        return f"sh.{code}"
    return f"sz.{code}"


# ── 全量 ticker 列表 ─────────────────────────────────────────────────────────

def fetch_all_tickers(market: str = "all") -> list[str]:
    """从 baostock query_stock_basic 获取全量 A 股 ticker 列表。"""
    import baostock as bs
    print("正在登录 baostock...", end=" ", flush=True)
    bs.login()
    print("✓")

    print("正在获取全量 A 股 ticker 列表...", end=" ", flush=True)
    rs = bs.query_stock_basic()
    rows = []
    while (rs.error_code == "0") and rs.next():
        rows.append(rs.get_row_data())
    bs.logout()

    df = pd.DataFrame(rows, columns=rs.fields)
    print(f"共 {len(df)} 条")

    # 只保留 A 股（type=1），过滤基金/债券等
    if "type" in df.columns:
        df = df[df["type"] == "1"]

    tickers = []
    for bs_code in df["code"].astype(str):
        t = _bs_to_internal(bs_code)
        if t is None:
            continue
        # 过滤北交所（bj. 前缀，baostock 里少见，但以防万一）
        code6 = t.split(".")[0]
        if code6[0] in ("4", "8") and not code6.startswith("688"):
            continue
        if market == "sh" and not t.endswith(".SH"):
            continue
        if market == "sz" and not t.endswith(".SZ"):
            continue
        tickers.append(t)

    print(f"有效 ticker: {len(tickers)} 只")
    return tickers


# ── 单只下载 ─────────────────────────────────────────────────────────────────

def _download_one(ticker: str, start: str, end: str, timeout: int = 20) -> tuple[str, str]:
    """
    返回 (ticker, status)。调用前需已完成 bs.login()，调用后不 logout。
    status: "cached" | "ok:<rows>" | "empty" | "error:<msg>"
    """
    import signal
    import baostock as bs

    cached = _load_from_disk_cache(ticker, start, end)
    if cached is not None and not cached.empty:
        return ticker, "cached"

    def _timeout_handler(_sig, _frame):
        raise TimeoutError(f"baostock query timeout after {timeout}s")

    prev = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(timeout)
    try:
        bs_code = _internal_to_bs(ticker)
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="2",  # 前复权 qfq
        )
        if rs.error_code != "0":
            signal.alarm(0)
            return ticker, "empty"
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        signal.alarm(0)

        if not rows:
            return ticker, "empty"

        df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
        df = df.replace("", float("nan"))
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.dropna(subset=["close"], inplace=True)

        if df.empty:
            return ticker, "empty"

        df["Date"] = pd.to_datetime(df["time"])
        df.set_index("Date", inplace=True)
        df.sort_index(inplace=True)
        df["ret"] = df["close"].pct_change() * 100

        _save_to_disk_cache(df, ticker, start, end)
        return ticker, f"ok:{len(df)}"

    except Exception as exc:
        signal.alarm(0)
        return ticker, f"error:{exc}"
    finally:
        signal.signal(signal.SIGALRM, prev)


# ── 主流程 ───────────────────────────────────────────────────────────────────

def run(tickers: list[str], start: str, end: str, workers: int = WORKERS) -> None:
    total  = len(tickers)
    done   = 0
    cached = 0
    ok     = 0
    empty  = 0
    failed = []

    print(f"\n{'='*55}")
    print(f"  全量 A 股价格下载（baostock）")
    print(f"  股票数: {total}  区间: {start} → {end}  并发: {workers}")
    print(f"{'='*55}\n")

    import baostock as bs
    bs.login()
    t0 = time.time()

    try:
        for ticker in tickers:
            _, status = _download_one(ticker, start, end)
            done += 1

            if status == "cached":
                cached += 1
            elif status.startswith("ok"):
                ok += 1
            elif status == "empty":
                empty += 1
                failed.append(ticker)
            else:
                failed.append(ticker)
                logger.warning("%s  %s", ticker, status)

            if done % 20 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta  = (total - done) / rate if rate > 0 else 0
                print(
                    f"  [{done:4d}/{total}]  "
                    f"新增:{ok}  缓存:{cached}  空:{empty}  失败:{len(failed)-empty}  "
                    f"速度:{rate:.1f}/s  ETA:{eta/60:.0f}min",
                )
    finally:
        bs.logout()

    elapsed = time.time() - t0
    print(f"\n\n{'='*55}")
    print(f"  完成！耗时 {elapsed/60:.1f} 分钟")
    print(f"  新增下载: {ok}")
    print(f"  已有缓存: {cached}")
    print(f"  无数据:   {empty}")
    print(f"  失败:     {len(failed) - empty}")
    print(f"{'='*55}")

    if failed:
        FAILED_LOG.write_text("\n".join(failed), encoding="utf-8")
        print(f"\n失败列表已写入: {FAILED_LOG}")
        print("可用 --retry-failed 重跑")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="全量 A 股历史价格批量下载（baostock）")
    parser.add_argument("--start",   default=START_DATE, help=f"开始日期（默认 {START_DATE}）")
    parser.add_argument("--end",     default=END_DATE,   help=f"结束日期（默认 {END_DATE}）")
    parser.add_argument(
        "--market", choices=["all", "sh", "sz"], default="all",
        help="只下载某个市场（默认 all）",
    )
    parser.add_argument(
        "--workers", type=int, default=WORKERS,
        help=f"并发线程数（默认 {WORKERS}）",
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help=f"从 {FAILED_LOG.name} 重试上次失败的 ticker",
    )
    args = parser.parse_args()

    if args.retry_failed:
        if not FAILED_LOG.exists():
            print(f"找不到失败列表: {FAILED_LOG}")
            return 1
        tickers = [t.strip() for t in FAILED_LOG.read_text().splitlines() if t.strip()]
        print(f"从失败列表加载 {len(tickers)} 只股票")
    else:
        tickers = fetch_all_tickers(market=args.market)

    if not tickers:
        print("没有可下载的 ticker")
        return 1

    run(tickers, start=args.start, end=args.end, workers=args.workers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
