# -*- coding: utf-8 -*-
"""
完整选股链入口

逻辑：
    1. 找热门行业（过去 lookback_days 涨幅前 top_n 名）
    2. 拿各行业成分股
    3. 技术过滤：均线多头排列 + 回踩锤子线买点
    4. 返回候选买点列表

用法：
    from backend.tools.stock_selector import select_stocks

    candidates = select_stocks("2024-01-15")
    for c in candidates:
        print(c)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def select_stocks(
    as_of_date: str,
    *,
    lookback_days: int = 60,
    top_n_sectors: int = 5,
    sma_fast: int = 20,
    sma_slow: int = 60,
    bias_low: float = -5.0,
    bias_high: float = 2.0,
    require_hammer: bool = True,
) -> list[dict]:
    """
    完整选股链：热门行业 → 成分股 → 技术过滤。

    参数：
        as_of_date:     回测日期，格式 YYYY-MM-DD
        lookback_days:  行业热度回看窗口，默认 60 个交易日（约 3 个月）
        top_n_sectors:  取热度前几名行业，默认 5
        sma_fast:       快速均线（默认 SMA20）
        sma_slow:       慢速均线（默认 SMA60）
        bias_low:       乖离率（价格偏离均线的百分比）下限，默认 -5%
        bias_high:      乖离率上限，默认 +2%
        require_hammer: 是否要求锤子线，默认 True

    返回：
        list of dict，每项含：
            ticker:      股票代码，如 "600519.SH"
            sector:      所属行业
            close:       当日收盘价
            sma_fast:    SMA20 值
            sma_slow:    SMA60 值
            bias_fast:   乖离率（%）
            is_hammer:   是否出现锤子线
            sector_return_pct: 所属行业过去 lookback_days 的涨幅（%）
    """
    from backend.tools.sector_tools import rank_hot_sectors, get_sector_constituents
    from backend.tools.technical_filter import screen_candidates

    # ── 第一步：热门行业 ──────────────────────────────────────────────────────
    hot_sectors = rank_hot_sectors(
        as_of_date=as_of_date,
        lookback_days=lookback_days,
        top_n=top_n_sectors,
    )

    if not hot_sectors:
        logger.warning("没有可用的行业热度数据，请先跑 seed_market_index_cache.py")
        return []

    logger.info("热门行业 [%s]: %s", as_of_date,
                [(s["name"], f"{s['return_pct']:+.1f}%") for s in hot_sectors])

    # ── 第二步：成分股 ────────────────────────────────────────────────────────
    sector_ticker_map: dict[str, list[str]] = {}
    for sector in hot_sectors:
        name = sector["name"]
        tickers = get_sector_constituents(name)
        if tickers:
            sector_ticker_map[name] = tickers
            logger.info("  %s: %d 只成分股", name, len(tickers))
        else:
            logger.warning("  %s: 成分股为空，跳过", name)

    if not sector_ticker_map:
        logger.warning("所有热门行业成分股均为空")
        return []

    # ── 第三步：技术过滤 ──────────────────────────────────────────────────────
    # 先去重（同一只股票可能属于多个行业）
    ticker_to_sector: dict[str, str] = {}
    for sector_name, tickers in sector_ticker_map.items():
        for t in tickers:
            if t not in ticker_to_sector:
                ticker_to_sector[t] = sector_name   # 保留第一个行业

    all_tickers = list(ticker_to_sector.keys())
    logger.info("去重后候选股票: %d 只", len(all_tickers))

    passed = screen_candidates(
        all_tickers, as_of_date,
        sma_fast=sma_fast,
        sma_slow=sma_slow,
        bias_low=bias_low,
        bias_high=bias_high,
        require_hammer=require_hammer,
    )

    # ── 附加行业信息 ──────────────────────────────────────────────────────────
    sector_return: dict[str, float] = {s["name"]: s["return_pct"] for s in hot_sectors}
    for item in passed:
        item["sector"] = ticker_to_sector.get(item["ticker"], "")
        item["sector_return_pct"] = sector_return.get(item["sector"], 0.0)

    # 按行业涨幅降序，同行业内按乖离率升序（越接近均线越优先）
    passed.sort(key=lambda x: (-x["sector_return_pct"], x["bias_fast"]))

    logger.info(
        "选股完成 [%s]: 热门行业 %d 个 → 候选 %d 只 → 通过 %d 只",
        as_of_date, len(hot_sectors), len(all_tickers), len(passed),
    )
    return passed


def print_candidates(candidates: list[dict]) -> None:
    """格式化打印候选买点。"""
    if not candidates:
        print("  （无候选）")
        return
    print(f"  {'代码':<12} {'行业':<8} {'收盘':>7} {'SMA20':>7} {'乖离率':>7} {'锤子线':<6} {'行业涨幅':>8}")
    print(f"  {'-'*60}")
    for c in candidates:
        print(
            f"  {c['ticker']:<12} {c['sector']:<8} "
            f"{c['close']:>7.2f} {c['sma_fast']:>7.2f} "
            f"{c['bias_fast']:>+6.1f}% {'✓' if c['is_hammer'] else '✗':<6} "
            f"{c['sector_return_pct']:>+7.1f}%"
        )
