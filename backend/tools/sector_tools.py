# -*- coding: utf-8 -*-
"""
行业热度排名 + 行业成分股获取

两个核心功能：
1. rank_hot_sectors(as_of_date, lookback_days, top_n)
   读取 theme_index_cache 中的行业指数，按 lookback_days 涨幅排名，返回前 top_n 个热门行业。

2. get_sector_constituents(sector_name)
   给定行业名称，返回该行业的成分股 ticker 列表。
   优先用 akshare THS（stock_board_industry_cons_ths），失败时返回空列表。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_THEME_CACHE_DIR  = _ROOT / "backend" / "data" / "theme_index_cache"
_MARKET_CACHE_DIR = _ROOT / "backend" / "data" / "market_index_cache"


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _load_latest_parquet(cache_dir: Path, prefix: str) -> pd.DataFrame | None:
    """
    在 cache_dir 里找以 prefix 开头的 parquet 文件，加载最新的一个。
    文件名格式：{prefix}_{start}_{end}.parquet
    """
    candidates = sorted(cache_dir.glob(f"{prefix}_*.parquet"))
    if not candidates:
        return None
    path = candidates[-1]   # 按文件名排序，取最后一个（end_date 最新）
    try:
        df = pd.read_parquet(path)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index, errors="coerce")
            df = df[df.index.notna()]
        df = df.sort_index()
        return df
    except Exception as exc:
        logger.warning("Failed to load %s: %s", path.name, exc)
        return None


def _safe_name(value: str) -> str:
    for ch in (".", "/", "\\", " ", ":"):
        value = value.replace(ch, "_")
    return value


# ── 行业热度排名 ──────────────────────────────────────────────────────────────

def rank_hot_sectors(
    as_of_date: str,
    lookback_days: int = 60,
    top_n: int = 5,
) -> list[dict]:
    """
    按过去 lookback_days 交易日的涨幅对已缓存行业指数排名。

    参数：
        as_of_date:    分析日期，格式 YYYY-MM-DD（回测时为回测当天）
        lookback_days: 回看窗口，默认 60 个交易日（约 3 个月）
        top_n:         返回前 N 名，默认 5

    返回：
        list of dict，每项包含：
            name:       行业名称（如 "银行"）
            return_pct: lookback 期间涨幅（%），保留 2 位小数
            last_close: 最后一个收盘价
            data_days:  实际用到的交易日数
    """
    as_of_ts = pd.Timestamp(as_of_date)
    results: list[dict] = []

    if not _THEME_CACHE_DIR.exists():
        logger.warning("theme_index_cache 目录不存在，请先跑 seed_market_index_cache.py")
        return []

    for parquet_path in sorted(_THEME_CACHE_DIR.glob("industry_*.parquet")):
        # 从文件名提取行业名称：industry_银行_2023-01-01_2026-06-11.parquet
        stem = parquet_path.stem   # industry_银行_2023-01-01_2026-06-11
        parts = stem.split("_")
        if len(parts) < 2:
            continue
        # 名称在 industry_ 和日期之间，日期格式 YYYY-MM-DD
        # stem: industry_{name}_{start}_{end}
        # 取中间部分（去掉 industry 前缀和最后两个日期段）
        name = "_".join(parts[1:-2]) if len(parts) > 3 else parts[1]

        try:
            df = pd.read_parquet(parquet_path)
            if not isinstance(df.index, pd.DatetimeIndex):
                df.index = pd.to_datetime(df.index, errors="coerce")
                df = df[df.index.notna()]
            df = df.sort_index()
        except Exception as exc:
            logger.warning("加载 %s 失败: %s", parquet_path.name, exc)
            continue

        # 截取到 as_of_date
        hist = df[df.index <= as_of_ts]
        if len(hist) < 2:
            continue

        # 取最近 lookback_days 个交易日
        window = hist.tail(lookback_days)
        if len(window) < 5:   # 数据太少，跳过
            continue

        close_col = next((c for c in ["close", "收盘", "收盘价"] if c in window.columns), None)
        if close_col is None:
            continue

        start_price = window[close_col].iloc[0]
        end_price   = window[close_col].iloc[-1]
        if start_price == 0 or pd.isna(start_price):
            continue

        ret_pct = round((end_price - start_price) / start_price * 100, 2)
        results.append({
            "name":       name,
            "return_pct": ret_pct,
            "last_close": round(float(end_price), 4),
            "data_days":  len(window),
        })

    if not results:
        logger.warning("没有可用的行业指数缓存，请先跑 seed_market_index_cache.py")
        return []

    results.sort(key=lambda x: x["return_pct"], reverse=True)
    return results[:top_n]


# ── 主题名 → 证监会行业代码前缀映射 ─────────────────────────────────────────
# baostock 用证监会分类，粒度比投资主题粗；技术过滤会进一步淘汰不相关股票

_SECTOR_TO_CSRC: dict[str, list[str]] = {
    "银行":     ["J66"],          # 货币金融服务（精准）
    "证券":     ["J67"],          # 资本市场服务（精准）
    "保险":     ["J68"],          # 保险业（精准）
    "白酒":     ["C15"],          # 酒、饮料和精制茶制造业（含啤酒/饮料）
    "白色家电": ["C38"],          # 电气机械和器材制造业（含冰箱/洗衣机/空调）
    "光伏设备": ["C38"],          # 同上（含光伏组件/逆变器）
    "电池":     ["C38"],          # 同上（含锂电/储能）
    "半导体":   ["C39"],          # 计算机、通信和其他电子设备制造业
    "通信设备": ["C39", "I63"],   # 电子设备 + 电信传输服务
    "计算机设备":["C39"],         # 同上
    "软件开发": ["I65"],          # 软件和信息技术服务业（精准）
    "医疗服务": ["Q83"],          # 卫生（含医院/诊所）
    "化学制药": ["C27"],          # 医药制造业（含化药/生物药/中药）
    "中药":     ["C27"],          # 同上
    # 概念板块：无直接映射，复用最相近的行业
    "白酒概念": ["C15"],
    "券商概念": ["J67"],
    "国企改革": ["J66", "J67", "D44"],  # 银行+券商+电力（国企密集行业）
    "人工智能": ["I65", "C39"],         # 软件+电子设备
    "算力概念": ["I65", "C39"],
    "创新药":   ["C27"],
    "固态电池": ["C38"],
}

# baostock 行业数据全局缓存（进程内复用，避免重复登录）
_BS_INDUSTRY_DF: pd.DataFrame | None = None


def _get_bs_industry_df() -> pd.DataFrame:
    """加载 baostock 全量行业分类，进程内缓存。"""
    global _BS_INDUSTRY_DF
    if _BS_INDUSTRY_DF is not None:
        return _BS_INDUSTRY_DF

    import baostock as bs
    lg = bs.login()
    try:
        rs = bs.query_stock_industry()
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()

    df = pd.DataFrame(rows, columns=rs.fields)
    _BS_INDUSTRY_DF = df
    return df


def _bs_code_to_internal(bs_code: str) -> str | None:
    """sh.600519 → 600519.SH，sz.000001 → 000001.SZ"""
    if "." not in bs_code:
        return None
    market, code = bs_code.split(".", 1)
    if not code.isdigit() or len(code) != 6:
        return None
    # 跳过北交所（4/8 开头，688 除外）
    if code[0] in ("4", "8") and not code.startswith("688"):
        return None
    if market == "sh":
        return f"{code}.SH"
    if market == "sz":
        return f"{code}.SZ"
    return None


# ── 行业成分股 ────────────────────────────────────────────────────────────────

def get_sector_constituents(sector_name: str) -> list[str]:
    """
    返回指定行业的成分股 ticker 列表（EvoTraders 内部格式）。

    数据源：baostock 证监会行业分类。
    通过 _SECTOR_TO_CSRC 映射表将投资主题名转换为证监会代码前缀，
    粒度比原始主题粗，技术过滤会进一步淘汰不相关股票。

    参数：
        sector_name: 行业名称，如 "银行"、"半导体"、"白酒"

    返回：
        list of str，如 ["600519.SH", "000858.SZ", ...]
        失败或无映射时返回空列表。
    """
    csrc_codes = _SECTOR_TO_CSRC.get(sector_name)
    if not csrc_codes:
        logger.warning("无证监会行业映射: %s，跳过", sector_name)
        return []

    try:
        df = _get_bs_industry_df()
        mask = df["industry"].apply(
            lambda ind: any(ind.startswith(c) for c in csrc_codes)
        )
        filtered = df[mask]

        tickers = []
        for bs_code in filtered["code"].astype(str):
            t = _bs_code_to_internal(bs_code)
            if t:
                tickers.append(t)

        logger.info("成分股 [%s → %s]: %d 只", sector_name, csrc_codes, len(tickers))
        return tickers

    except Exception as exc:
        logger.warning("获取行业成分股失败 [%s]: %s", sector_name, exc)
        return []


# ── 便捷入口：热门行业 + 成分股一步拿 ────────────────────────────────────────

def get_hot_sector_stocks(
    as_of_date: str,
    lookback_days: int = 60,
    top_n: int = 5,
) -> dict[str, list[str]]:
    """
    返回热门行业及其成分股。

    返回：
        dict，key = 行业名称，value = 成分股 ticker 列表
        例：{"银行": ["601398.SH", ...], "半导体": ["600760.SH", ...]}
    """
    hot = rank_hot_sectors(as_of_date=as_of_date, lookback_days=lookback_days, top_n=top_n)
    result: dict[str, list[str]] = {}
    for item in hot:
        name = item["name"]
        stocks = get_sector_constituents(name)
        result[name] = stocks
        logger.info(
            "行业: %-10s  涨幅: %+.1f%%  成分股: %d 只",
            name, item["return_pct"], len(stocks),
        )
    return result
