# -*- coding: utf-8 -*-
"""
数据预下载脚本
在跑 backtest 之前先把历史价格数据拉到磁盘缓存，
之后 backtest 全程读磁盘，不需要网络。

用法:
    python download_data.py --start 2023-10-01 --end 2024-03-31
"""
# ── 代理绕过：必须在所有其他 import 之前 ─────────────────────────────────────
import os as _os
_DOMESTIC_NO_PROXY = "dashscope.aliyuncs.com,*.aliyuncs.com,aliyuncs.com,eastmoney.com,*.eastmoney.com,10jqka.com.cn,*.10jqka.com.cn,finance.sina.com.cn,*.sina.com.cn,localhost,127.0.0.1"
_os.environ["NO_PROXY"] = _DOMESTIC_NO_PROXY
_os.environ["no_proxy"] = _DOMESTIC_NO_PROXY
# ─────────────────────────────────────────────────────────────────────────────

import argparse
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

import akshare as ak

from backend.config.env_config import get_env_list
from backend.data.historical_price_manager import (
    _load_akshare,
    _load_from_disk_cache,
    _save_to_disk_cache,
)
from backend.data.historical_price_manager import _to_akshare_symbol
from backend.tools.data_tools import (
    _load_fin_cache,
    _save_fin_cache,
    _akshare_call,
)


def download_financial(tickers):
    """预下载财务数据（同花顺财务摘要、现金流、公司基本信息）"""
    print(f"\n── 财务数据 ──────────────────────────────────────")
    for ticker in tickers:
        ak_symbol = _to_akshare_symbol(ticker)
        bare = ak_symbol.replace("SH", "").replace("SZ", "")

        # 1. 同花顺财务摘要
        _download_fin_item(
            ticker, "financial_abstract",
            lambda: _akshare_call(ak.stock_financial_abstract_ths, symbol=ak_symbol),
        )

        # 2. 现金流量表（东方财富报告期 → 东方财富年度 → 同花顺）
        em_cached  = _load_fin_cache(ticker, "cash_flow")
        ths_cached = _load_fin_cache(ticker, "cash_flow_ths")

        if em_cached is not None or ths_cached is not None:
            src = "em" if em_cached is not None else "ths"
            print(f"  ✓ {ticker}/cash_flow: 缓存命中({src})，跳过")
        else:
            def _fetch_cash_flow_em(sym=ak_symbol):
                for fn_name in ("stock_cash_flow_sheet_by_report_em",
                                "stock_cash_flow_sheet_by_yearly_em"):
                    fn = getattr(ak, fn_name, None)
                    if fn is None:
                        continue
                    try:
                        df = _akshare_call(fn, symbol=sym)
                        if df is not None and not df.empty:
                            return df
                    except Exception:
                        continue
                return None

            _download_fin_item(ticker, "cash_flow", _fetch_cash_flow_em)

            # 东方财富失败时，用同花顺补
            if _load_fin_cache(ticker, "cash_flow") is None:
                _download_fin_item(
                    ticker, "cash_flow_ths",
                    lambda sym=ak_symbol: _akshare_call(
                        ak.stock_financial_cash_ths,
                        symbol=sym,
                        indicator="按报告期",
                    ),
                )

        # 3. 公司公告（东方财富 stock_individual_notice_report，支持完整历史回溯）
        #    market_cap 已改为从 net_income/EPS 推算，不再需要 company_info
        _download_fin_item(
            ticker, "notice",
            lambda sym=bare: _akshare_call(
                ak.stock_individual_notice_report,
                security=sym,
                symbol="全部",
            ),
        )


def _download_fin_item(ticker, data_type, fetch_fn, retries=2):
    cached = _load_fin_cache(ticker, data_type)
    if cached is not None:
        print(f"  ✓ {ticker}/{data_type}: 缓存命中，跳过")
        return
    print(f"  📥 {ticker}/{data_type} ...", end=" ", flush=True)
    for attempt in range(retries + 1):
        try:
            df = fetch_fn()
            if df is not None and not df.empty:
                _save_fin_cache(df, ticker, data_type)
                print(f"✅ {len(df)} 行")
                return
        except Exception as e:
            if attempt < retries:
                print(f"重试({attempt+1})...", end=" ", flush=True)
                continue
            print(f"❌ {e}")
            return
    print("❌ 返回空")


def download(tickers, start_date, end_date):
    print(f"\n{'='*50}")
    print(f"  EvoTraders 数据预下载")
    print(f"  Tickers : {tickers}")
    print(f"  区间    : {start_date} → {end_date}")
    print(f"{'='*50}\n")

    # ── 历史价格 ──────────────────────────────────────────────────────────
    print("── 历史价格 ──────────────────────────────────────")
    for ticker in tickers:
        print(f"  📥 {ticker} ...", end=" ", flush=True)

        cached = _load_from_disk_cache(ticker, start_date, end_date)
        if cached is not None and not cached.empty:
            print(f"缓存命中，{len(cached)} 条，跳过")
            continue

        df = _load_akshare(ticker, start_date, end_date)
        if df is None or df.empty:
            print("❌ 失败（akshare 返回空）")
            continue

        _save_to_disk_disk_cache_or_log(df, ticker, start_date, end_date)
        print(f"✅ {len(df)} 条已缓存")

    # ── 财务数据 ──────────────────────────────────────────────────────────
    download_financial(tickers)

    print(f"\n{'='*50}")
    print("  下载完成，可以跑 backtest 了")
    print(f"{'='*50}\n")


def _save_to_disk_disk_cache_or_log(df, ticker, start_date, end_date):
    try:
        _save_to_disk_cache(df, ticker, start_date, end_date)
    except Exception as e:
        print(f"  ⚠️  写磁盘失败: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2023-10-01", help="开始日期，建议比 backtest 早 90 天")
    parser.add_argument("--end",   default="2024-03-31", help="结束日期，建议比 backtest 晚一点")
    args = parser.parse_args()

    tickers = get_env_list("TICKERS", ["600519.SH", "601398.SH"])
    download(tickers, args.start, args.end)
