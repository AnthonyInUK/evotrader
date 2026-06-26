#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 PE / PB / market_cap 字段是否正确写入。

用法（本地运行，需要价格缓存已存在）：

    cd evotraders
    python scripts/verify_valuation_fields.py

    # 换一只股票或日期
    python scripts/verify_valuation_fields.py --ticker 000858 --date 2024-03-31
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# A 股路径不使用 Finnhub/FD key，但 get_config() 在函数入口无条件检查。
# 设一个占位 key 让 config 初始化通过，不影响实际数据路径。
import os
os.environ.setdefault("FINNHUB_API_KEY", "dummy_for_a_share_test")

from backend.tools.data_tools import get_financial_metrics, get_prices  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="600519", help="股票代码（默认贵州茅台）")
    parser.add_argument("--date", default="2024-03-31", help="报告期截止日（默认 2024-03-31）")
    args = parser.parse_args()

    ticker = args.ticker
    end_date = args.date

    print(f"\n{'='*55}")
    print(f"  股票: {ticker}  |  报告期: {end_date}")
    print(f"{'='*55}")

    # ── 1. 拉财务指标 ─────────────────────────────────────────────
    metrics = get_financial_metrics(ticker=ticker, end_date=end_date)
    if not metrics:
        print("❌  get_financial_metrics 返回空，检查财务缓存是否存在")
        return

    m = metrics[0]
    print(f"\n【财务原始字段】")
    print(f"  EPS（基本每股收益）: {m.earnings_per_share}")
    print(f"  BVPS（每股净资产）:   {m.book_value_per_share}")

    # ── 2. 拉当日收盘价 ───────────────────────────────────────────
    prices = get_prices(ticker=ticker, start_date=end_date, end_date=end_date)
    if not prices:
        from datetime import datetime, timedelta
        fb = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        prices = get_prices(ticker=ticker, start_date=fb, end_date=end_date)
    close = prices[-1].close if prices else None
    print(f"  收盘价（{prices[-1].time if prices else 'N/A'}）:  {close}")

    # ── 3. 打印注入后的估值字段 ───────────────────────────────────
    print(f"\n【注入后的估值字段】")
    print(f"  PE（市盈率）:   {m.price_to_earnings_ratio}")
    print(f"  PB（市净率）:   {m.price_to_book_ratio}")
    print(f"  market_cap:    {m.market_cap}")

    # ── 4. 手动复算，暴露任何逻辑偏差 ────────────────────────────
    print(f"\n【手动复算（以上方收盘价为基准）】")
    if close and m.earnings_per_share and m.earnings_per_share > 0:
        manual_pe = round(close / m.earnings_per_share, 2)
        match = "✓" if manual_pe == m.price_to_earnings_ratio else "✗ 不一致！"
        print(f"  PE = {close} / {m.earnings_per_share} = {manual_pe}  {match}")
    else:
        print("  PE: EPS 为 None 或 ≤0，跳过")

    if close and m.book_value_per_share and m.book_value_per_share > 0:
        manual_pb = round(close / m.book_value_per_share, 2)
        match = "✓" if manual_pb == m.price_to_book_ratio else "✗ 不一致！"
        print(f"  PB = {close} / {m.book_value_per_share} = {manual_pb}  {match}")
    else:
        print("  PB: BVPS 为 None 或 ≤0，跳过")

    # ── 5. 量级参考（不用精确，10% 以内算对） ────────────────────
    print(f"\n【量级参考】")
    print("  market_cap 单位应为「元」。贵州茅台 2024Q1 约 2.0-2.3 万亿")
    if m.market_cap:
        wan_yi = m.market_cap / 1e12
        print(f"  当前值: {m.market_cap:.2e} 元  ≈  {wan_yi:.1f} 万亿")
    else:
        print("  market_cap 为 None，检查 _get_a_share_market_cap 或价格缓存")

    print()


if __name__ == "__main__":
    main()
