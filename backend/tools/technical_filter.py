# -*- coding: utf-8 -*-
"""
技术结构过滤模块

两道门槛：
1. 趋势确认（第一道）：close > SMA20 > SMA60，均线多头排列
2. 回踩买点（第二道）：乖离率（close / SMA20 - 1）在 [-5%, +2%] 内，
   且当日 K 线满足弱势收盘条件（下影线 / 实体缩量等）

用法：
    from backend.tools.technical_filter import filter_by_trend, filter_by_pullback

    # 第一道门：趋势
    trend_ok = filter_by_trend(tickers, as_of_date)

    # 第二道门：回踩（在 trend_ok 结果上再过滤）
    candidates = filter_by_pullback(trend_ok, as_of_date)
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]


# ── 价格加载 ──────────────────────────────────────────────────────────────────

def _load_prices(ticker: str, as_of_date: str, lookback: int = 120) -> pd.DataFrame | None:
    """
    从磁盘缓存加载 ticker 的前复权日线，截止 as_of_date，取最近 lookback 条。
    返回 DataFrame（index=DatetimeIndex，含 close / open / high / low / volume），
    或 None（缓存不存在 / 数据不足）。
    """
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "historical_price_manager",
        _ROOT / "backend" / "data" / "historical_price_manager.py",
    )
    _hpm = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_hpm)

    # 往前推 lookback * 2 个日历日，保证交易日够用
    from datetime import datetime, timedelta
    end = as_of_date
    start = (datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=lookback * 2)).strftime("%Y-%m-%d")

    df = _hpm._load_from_disk_cache(ticker, start, end)
    if df is None or df.empty:
        return None

    # 统一列名
    col_map = {"Close": "close", "Open": "open", "High": "high",
               "Low": "low", "Volume": "volume"}
    df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

    if "close" not in df.columns:
        return None

    # 截止 as_of_date
    df = df[df.index <= pd.Timestamp(as_of_date)].copy()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["close"]).sort_index()

    return df.tail(lookback) if len(df) >= 5 else None


# ── 指标计算 ──────────────────────────────────────────────────────────────────

def _calc_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def _bias_rate(close: float, sma: float) -> float:
    """乖离率 = (close / SMA - 1) * 100，单位 %"""
    if sma == 0 or pd.isna(sma):
        return float("nan")
    return (close / sma - 1) * 100


# ── 第一道门：趋势确认 ────────────────────────────────────────────────────────

def filter_by_trend(
    tickers: list[str],
    as_of_date: str,
    *,
    sma_fast: int = 20,
    sma_slow: int = 60,
) -> list[dict]:
    """
    第一道门：均线多头排列过滤。

    条件：
        close[-1] > SMA{sma_fast}[-1] > SMA{sma_slow}[-1]

    参数：
        tickers:    候选 ticker 列表（EvoTraders 格式，如 "600519.SH"）
        as_of_date: 回测日期（只用该日期及之前的数据）
        sma_fast:   快速均线周期，默认 20
        sma_slow:   慢速均线周期，默认 60

    返回：
        list of dict，每项含：
            ticker:    股票代码
            close:     当日收盘价
            sma_fast:  SMA20 值
            sma_slow:  SMA60 值
            bias_fast: close 对 SMA20 的乖离率（%）
    """
    passed: list[dict] = []
    missing = 0

    for ticker in tickers:
        df = _load_prices(ticker, as_of_date, lookback=sma_slow + 10)
        if df is None or len(df) < sma_slow:
            missing += 1
            continue

        sma_f = _calc_sma(df["close"], sma_fast)
        sma_s = _calc_sma(df["close"], sma_slow)

        last_close = df["close"].iloc[-1]
        last_sma_f = sma_f.iloc[-1]
        last_sma_s = sma_s.iloc[-1]

        if pd.isna(last_sma_f) or pd.isna(last_sma_s):
            continue

        if last_close > last_sma_f > last_sma_s:
            passed.append({
                "ticker":    ticker,
                "close":     round(float(last_close), 3),
                "sma_fast":  round(float(last_sma_f), 3),
                "sma_slow":  round(float(last_sma_s), 3),
                "bias_fast": round(_bias_rate(last_close, last_sma_f), 2),
            })

    logger.info(
        "趋势过滤 [%s]: 输入 %d → 通过 %d（缺数据 %d）",
        as_of_date, len(tickers), len(passed), missing,
    )
    return passed


# ── K 线形态识别 ──────────────────────────────────────────────────────────────

def _is_hammer(
    row: pd.Series,
    lower_min_pct: float = 0.6,
    upper_max_pct: float = 0.2,
) -> bool:
    """
    判断单根 K 线是否为锤子线。

    用"占整根 K 线总波幅的比例"来判断，避免实体趋近零（十字星）时失效。

    条件：
        下影线 / 总波幅（high - low）≥ lower_min_pct   默认 60%
        上影线 / 总波幅              ≤ upper_max_pct   默认 20%

    例：open=10, high=10.2, low=8, close=10.1
        总波幅=2.2，下影线=2.0（91%）✓，上影线=0.1（5%）✓ → 锤子线
    """
    try:
        o = float(row.get("open",  float("nan")))
        h = float(row.get("high",  float("nan")))
        l = float(row.get("low",   float("nan")))
        c = float(row["close"])
    except (TypeError, ValueError):
        return False

    if any(pd.isna(v) for v in [o, h, l, c]):
        return False

    total_range = h - l
    if total_range <= 0:
        return False   # 一字板，无法判断

    lower_shadow = min(o, c) - l     # 实体底部到最低价
    upper_shadow = h - max(o, c)     # 最高价到实体顶部

    return (
        (lower_shadow / total_range >= lower_min_pct) and
        (upper_shadow / total_range <= upper_max_pct)
    )


# ── 第二道门：回踩买点 ────────────────────────────────────────────────────────

def filter_by_pullback(
    trend_passed: list[dict],
    as_of_date: str,
    *,
    bias_low: float = -5.0,
    bias_high: float = 2.0,
    require_hammer: bool = True,
    hammer_shadow_ratio: float = 2.0,
) -> list[dict]:
    """
    第二道门：回踩 SMA20 买点过滤。

    条件：
        1. 乖离率（价格偏离SMA20的百分比）∈ [bias_low%, bias_high%]
           默认 [-5%, +2%]：价格已回踩到均线附近
        2. （可选）当日出现锤子线：被砸下去又弹回来，
           说明均线附近有真实买盘承接

    参数：
        trend_passed:        filter_by_trend 的输出
        as_of_date:          回测日期
        bias_low/bias_high:  乖离率区间（%）
        require_hammer:      是否要求锤子线形态，默认 True
        hammer_shadow_ratio: 下影线至少是实体的几倍，默认 2.0

    返回：
        list of dict（追加 is_hammer 字段）
    """
    passed: list[dict] = []

    for item in trend_passed:
        ticker = item["ticker"]
        bias   = item["bias_fast"]

        if pd.isna(bias) or not (bias_low <= bias <= bias_high):
            continue

        row = item.copy()
        row["is_hammer"] = False

        if require_hammer:
            df = _load_prices(ticker, as_of_date, lookback=5)
            if df is not None and not df.empty:
                row["is_hammer"] = _is_hammer(
                    df.iloc[-1], shadow_ratio=hammer_shadow_ratio
                )

        if require_hammer and not row["is_hammer"]:
            continue

        passed.append(row)

    logger.info(
        "回踩过滤 [%s]: 输入 %d → 通过 %d（乖离 [%.1f%%, %.1f%%]，锤子线=%s）",
        as_of_date, len(trend_passed), len(passed), bias_low, bias_high, require_hammer,
    )
    return passed


# ── 卖点监控 ─────────────────────────────────────────────────────────────────

def check_sell_signal(
    ticker: str,
    as_of_date: str,
    entry_price: float,
    *,
    sma_exit: int = 5,
    no_new_high_days: int = 3,
) -> dict:
    """
    卖点检查：满足任一条件即触发卖出信号。

    条件（OR 关系）：
        1. close < SMA{sma_exit}（跌破 5 日均线）
        2. 连续 no_new_high_days 天未创新高（高点不再突破）

    返回：
        dict，含 sell: bool, reason: str | None
    """
    df = _load_prices(ticker, as_of_date, lookback=sma_exit + no_new_high_days + 5)
    if df is None or len(df) < sma_exit + 1:
        return {"sell": False, "reason": None, "ticker": ticker}

    sma_e = _calc_sma(df["close"], sma_exit)
    last_close = df["close"].iloc[-1]
    last_sma_e = sma_e.iloc[-1]

    # 条件 1：跌破 SMA5
    if not pd.isna(last_sma_e) and last_close < last_sma_e:
        return {"sell": True, "reason": f"close({last_close:.2f}) < SMA{sma_exit}({last_sma_e:.2f})", "ticker": ticker}

    # 条件 2：连续 N 天未创新高
    recent = df.tail(no_new_high_days + 1)
    if "high" in recent.columns:
        highs = pd.to_numeric(recent["high"], errors="coerce").dropna()
        if len(highs) >= no_new_high_days + 1:
            peak = highs.iloc[0]  # N 天前的高点
            if all(h <= peak for h in highs.iloc[1:]):
                return {
                    "sell": True,
                    "reason": f"连续 {no_new_high_days} 天未创新高（峰值 {peak:.2f}）",
                    "ticker": ticker,
                }

    return {"sell": False, "reason": None, "ticker": ticker}


# ── 便捷入口：完整两道门一步过 ───────────────────────────────────────────────

def screen_candidates(
    tickers: list[str],
    as_of_date: str,
    *,
    sma_fast: int = 20,
    sma_slow: int = 60,
    bias_low: float = -5.0,
    bias_high: float = 2.0,
    require_hammer: bool = True,
) -> list[dict]:
    """
    完整两道门筛选：趋势确认 → 回踩买点（锤子线）。

    返回：list of dict，每项含 ticker / close / sma_fast / sma_slow /
                             bias_fast / is_hammer
    """
    trend_ok = filter_by_trend(
        tickers, as_of_date,
        sma_fast=sma_fast, sma_slow=sma_slow,
    )
    return filter_by_pullback(
        trend_ok, as_of_date,
        bias_low=bias_low, bias_high=bias_high,
        require_hammer=require_hammer,
    )
