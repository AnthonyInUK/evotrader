# -*- coding: utf-8 -*-
"""
Historical Price Manager for backtest mode

数据优先级：
  1. akshare（A股实时拉取，带磁盘缓存）
  2. 本地 CSV（ret_data/ 目录，向后兼容美股数据）
  3. 保留上次价格（数据缺口时不让回测崩溃）

A股 ticker 格式约定（EvoTraders 内部）：
  - 沪市：600519.SH
  - 深市：000001.SZ
  - 转换为 akshare 格式：去掉后缀，纯6位数字
"""
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# 本地 CSV 目录（向后兼容）
_DATA_DIR = Path(__file__).parent / "ret_data"

# akshare 磁盘缓存目录（避免每次回测都重新拉取）
_CACHE_DIR = Path(__file__).parent / "akshare_cache"


def _is_a_share(symbol: str) -> bool:
    """
    判断是否是A股 ticker。

    约定格式：
      - 600519.SH  → 沪市
      - 000001.SZ  → 深市
      - 纯6位数字  → 也视为A股（兼容旧格式）
    """
    return bool(re.match(r"^\d{6}(\.(SH|SZ|BJ))?$", symbol, re.IGNORECASE))


def _to_akshare_symbol(symbol: str) -> str:
    """
    将 EvoTraders 内部 ticker 转成 akshare 接受的格式。

    akshare stock_zh_a_hist() 接受纯6位数字，不要后缀。
    例：600519.SH → 600519
        000001.SZ → 000001
        000001    → 000001（已经是纯数字，直接返回）
    """
    return symbol.split(".")[0]


def _price_cache_only() -> bool:
    raw = os.getenv("A_SHARE_PRICE_CACHE_ONLY", "")
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_akshare(
    symbol: str,
    start_date: str,
    end_date: str,
) -> Optional[pd.DataFrame]:
    """
    通过 akshare 拉取A股日线数据（前复权）。

    返回的 DataFrame 列：open, close, high, low, volume, time, ret
    索引：DatetimeIndex（与本地 CSV 格式对齐，方便统一处理）

    ⚠️ 问题记录 #1：akshare 返回的列名是中文
       ak.stock_zh_a_hist() 返回列名：日期/开盘/收盘/最高/最低/成交量/...
       需要手动重命名成英文，否则 set_date() 里的 row["open"] 会 KeyError。

    ⚠️ 问题记录 #2：日期列格式不统一
       akshare 返回的"日期"列有时是 str，有时是 Timestamp，
       用 pd.to_datetime() 统一转换。
    """
    try:
        import akshare as ak  # 懒加载，没装 akshare 也能用 CSV 模式
    except ImportError:
        logger.warning(
            "akshare 未安装，跳过A股数据拉取。"
            "请运行：pip install akshare"
        )
        return None

    ak_symbol = _to_akshare_symbol(symbol)
    # akshare 日期格式：YYYYMMDD（无横线）
    ak_start = start_date.replace("-", "")
    ak_end = end_date.replace("-", "")

    # ── 主接口：东方财富 stock_zh_a_hist ────────────────────────────
    df = None
    try:
        df = ak.stock_zh_a_hist(
            symbol=ak_symbol,
            period="daily",
            start_date=ak_start,
            end_date=ak_end,
            adjust="qfq",
        )
    except Exception as e:
        logger.warning(f"akshare 东方财富接口拉取 {symbol} 失败: {e}，尝试备用接口...")

    # ── 备用接口：新浪 stock_zh_a_daily ─────────────────────────────
    if df is None or df.empty:
        try:
            # 新浪接口需要加交易所前缀：sh/sz
            if ak_symbol.startswith("6"):
                sina_symbol = f"sh{ak_symbol}"
            else:
                sina_symbol = f"sz{ak_symbol}"
            df_sina = ak.stock_zh_a_daily(
                symbol=sina_symbol,
                start_date=ak_start,
                end_date=ak_end,
                adjust="qfq",
            )
            if df_sina is not None and not df_sina.empty:
                # 新浪接口列名已是英文，做映射对齐
                sina_col_map = {
                    "date": "time",
                    "open": "open",
                    "close": "close",
                    "high": "high",
                    "low": "low",
                    "volume": "volume",
                }
                df_sina = df_sina.rename(columns=sina_col_map)
                df_sina["ret"] = df_sina["close"].pct_change() * 100
                df = df_sina
                logger.info(f"备用接口成功拉取 {symbol}，{len(df)} 条")
        except Exception as e2:
            logger.warning(f"akshare 备用接口拉取 {symbol} 也失败: {e2}")

    if df is None or df.empty:
        logger.warning(f"akshare 拉取 {symbol} 失败（所有接口均无数据）")
        return None

    # ── 列名标准化（中文 → 英文，处理东方财富接口）────────────────
    col_map = {
        "日期": "time",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "涨跌幅": "ret",
    }
    df = df.rename(columns=col_map)

    # 保留我们需要的列（其余丢弃）
    keep_cols = [c for c in ["time", "open", "close", "high", "low", "volume", "ret"] if c in df.columns]
    df = df[keep_cols].copy()

    # ── 索引处理 ──────────────────────────────────────────────────
    if "time" in df.columns:
        df["Date"] = pd.to_datetime(df["time"])
        df.set_index("Date", inplace=True)
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df.sort_index(inplace=True)

    return df


def _cache_path(symbol: str, start_date: str, end_date: str) -> Path:
    """生成磁盘缓存文件路径"""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_symbol = symbol.replace(".", "_")
    return _CACHE_DIR / f"{safe_symbol}_{start_date}_{end_date}.parquet"


def _load_from_disk_cache(
    symbol: str,
    start_date: str,
    end_date: str,
) -> Optional[pd.DataFrame]:
    """
    从磁盘缓存读取 akshare 数据。

    使用 parquet 格式（比 CSV 快 5-10x，且保留数据类型）。

    ⚠️ 问题记录 #3：parquet 需要 pyarrow 或 fastparquet
       如果没装，降级为 CSV 缓存。
    """
    cache_file = _cache_path(symbol, start_date, end_date)
    if cache_file.exists():
        try:
            df = pd.read_parquet(cache_file)
            logger.info(f"从磁盘缓存读取 {symbol}: {cache_file.name}")
            return df
        except Exception as e:
            logger.warning(f"读取缓存失败 {symbol}: {e}，将重新拉取")
            cache_file.unlink(missing_ok=True)

    # 兼容“大区间缓存服务小区间回测”的情况：
    # 例如本地已有 2024-01-01~2024-03-31，而本次只回测 2024-01-02~2024-01-05。
    safe_symbol = symbol.replace(".", "_")
    requested_start = pd.Timestamp(start_date)
    requested_end = pd.Timestamp(end_date)

    for candidate in sorted(_CACHE_DIR.glob(f"{safe_symbol}_*.parquet")):
        stem = candidate.stem
        prefix = f"{safe_symbol}_"
        if not stem.startswith(prefix):
            continue
        remainder = stem[len(prefix) :]
        parts = remainder.split("_")
        if len(parts) < 2:
            continue

        cached_start_raw, cached_end_raw = parts[-2], parts[-1]
        try:
            cached_start = pd.Timestamp(cached_start_raw)
            cached_end = pd.Timestamp(cached_end_raw)
        except Exception:
            continue

        if cached_start > requested_start or cached_end < requested_end:
            continue

        try:
            df = pd.read_parquet(candidate)
            if df.empty:
                continue
            sliced = df.loc[
                (df.index >= requested_start) & (df.index <= requested_end)
            ].copy()
            if sliced.empty:
                continue
            logger.info(
                "从磁盘缓存复用 %s: %s 覆盖 %s ~ %s",
                symbol,
                candidate.name,
                start_date,
                end_date,
            )
            return sliced
        except Exception as e:
            logger.warning(f"读取缓存失败 {symbol}: {e}")

    return None


def _load_any_disk_cache(symbol: str) -> Optional[pd.DataFrame]:
    """
    Load any existing parquet cache for a symbol.

    This is a cache-only experiment fallback: a shorter warmup range is often
    still enough for smoke tests, and it is preferable to failing or touching
    remote quote APIs during controlled experiments.
    """
    safe_symbol = symbol.replace(".", "_")
    for candidate in sorted(_CACHE_DIR.glob(f"{safe_symbol}_*.parquet"), reverse=True):
        try:
            df = pd.read_parquet(candidate)
            if df is not None and not df.empty:
                logger.warning(
                    "使用已有磁盘缓存 %s 代替完整请求区间（可能缺少部分warmup）",
                    candidate.name,
                )
                return df
        except Exception as e:
            logger.warning(f"读取缓存失败 {symbol}: {e}")
    return None


def _save_to_disk_cache(
    df: pd.DataFrame,
    symbol: str,
    start_date: str,
    end_date: str,
) -> None:
    """将 akshare 数据写入磁盘缓存"""
    cache_file = _cache_path(symbol, start_date, end_date)
    try:
        df.to_parquet(cache_file)
        logger.info(f"缓存已写入: {cache_file.name}")
    except Exception as e:
        logger.warning(f"写入缓存失败 {symbol}: {e}")


class HistoricalPriceManager:
    """
    回测模式历史价格管理器

    数据优先级（自动降级）：
      akshare（网络）→ 磁盘缓存 → 本地 CSV → 保留上次价格

    使用示例：
        mgr = HistoricalPriceManager()
        mgr.subscribe(["600519.SH", "000001.SZ"])
        mgr.preload_data("2024-01-01", "2024-12-31")

        for date in trading_dates:
            mgr.set_date(date)
            mgr.emit_open_prices()   # 触发分析师 Agent
            mgr.emit_close_prices()  # 触发 PM Agent 决策
    """

    def __init__(self, use_akshare: bool = True):
        """
        Args:
            use_akshare: 是否启用 akshare 数据源（默认True）。
                         设为 False 时退化为纯 CSV 模式（离线/测试用）。
        """
        self.subscribed_symbols: List[str] = []
        self.price_callbacks: List[Callable] = []
        self._price_cache: Dict[str, pd.DataFrame] = {}
        self._current_date: Optional[str] = None
        self.latest_prices: Dict[str, float] = {}
        self.open_prices: Dict[str, float] = {}
        self.close_prices: Dict[str, float] = {}
        self.running: bool = False
        self.use_akshare: bool = use_akshare

    # ──────────────────────────────────────────────────────────────
    # 订阅管理
    # ──────────────────────────────────────────────────────────────

    def subscribe(self, symbols: List[str]) -> None:
        """订阅 ticker 列表"""
        for symbol in symbols:
            if symbol not in self.subscribed_symbols:
                self.subscribed_symbols.append(symbol)

    def unsubscribe(self, symbols: List[str]) -> None:
        """取消订阅"""
        for symbol in symbols:
            if symbol in self.subscribed_symbols:
                self.subscribed_symbols.remove(symbol)
                self._price_cache.pop(symbol, None)

    def add_price_callback(self, callback: Callable) -> None:
        """注册价格更新回调"""
        self.price_callbacks.append(callback)

    # ──────────────────────────────────────────────────────────────
    # 数据加载（三级降级链）
    # ──────────────────────────────────────────────────────────────

    def _load_from_akshare_with_cache(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> Optional[pd.DataFrame]:
        """
        akshare 数据加载，带磁盘缓存。

        流程：
          1. 尝试读磁盘缓存（parquet）
          2. 缓存未命中 → 调用 akshare API
          3. 拉取成功 → 写磁盘缓存
        """
        # 非A股不走 akshare（如 AAPL 等美股 ticker）
        if not _is_a_share(symbol):
            return None

        # 尝试磁盘缓存
        cached = _load_from_disk_cache(symbol, start_date, end_date)
        if cached is not None:
            return cached

        if _price_cache_only():
            fallback = _load_any_disk_cache(symbol)
            if fallback is not None:
                return fallback
            logger.warning(
                "A_SHARE_PRICE_CACHE_ONLY=1，跳过 akshare 网络拉取: %s",
                symbol,
            )
            return None

        # 网络拉取
        logger.info(f"通过 akshare 拉取 {symbol} ({start_date} ~ {end_date})")
        df = _load_akshare(symbol, start_date, end_date)

        if df is not None and not df.empty:
            _save_to_disk_cache(df, symbol, start_date, end_date)
            return df

        # akshare 失败：退回用磁盘上任意现有 parquet（保证 backtest 能跑起来）
        return _load_any_disk_cache(symbol)

    def _load_from_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        从本地 CSV 加载（向后兼容美股数据）。

        CSV 文件名规则：{symbol}.csv（如 AAPL.csv）
        A股 ticker 中的点会影响文件名，故转换：600519.SH → 600519_SH.csv
        """
        # A股 ticker 文件名转换
        csv_name = symbol.replace(".", "_")
        csv_path = _DATA_DIR / f"{csv_name}.csv"

        # 兼容旧格式（AAPL.csv 不含点的直接用）
        if not csv_path.exists():
            csv_path = _DATA_DIR / f"{symbol}.csv"

        if not csv_path.exists():
            return None

        try:
            df = pd.read_csv(csv_path)
            if df.empty or "time" not in df.columns:
                return None

            df["Date"] = pd.to_datetime(df["time"])
            df.set_index("Date", inplace=True)
            df.sort_index(inplace=True)
            return df
        except Exception as e:
            logger.warning(f"读取 CSV 失败 {symbol}: {e}")
            return None

    def preload_data(self, start_date: str, end_date: str) -> None:
        """
        预加载所有已订阅 ticker 的历史数据到内存。

        在回测开始前调用一次，之后 set_date() 从内存中取数据，
        避免每天都触发 IO。

        Args:
            start_date: 格式 "YYYY-MM-DD"
            end_date:   格式 "YYYY-MM-DD"
        """
        logger.info(f"预加载历史数据: {start_date} → {end_date}")

        for symbol in self.subscribed_symbols:
            if symbol in self._price_cache:
                continue  # 已缓存，跳过

            df = None

            # 第1优先级：akshare（仅A股）
            if self.use_akshare and _is_a_share(symbol):
                df = self._load_from_akshare_with_cache(
                    symbol, start_date, end_date
                )
                if df is not None and not df.empty:
                    self._price_cache[symbol] = df
                    logger.info(
                        f"[akshare] {symbol}: {len(df)} 条记录 "
                        f"({df.index[0].date()} ~ {df.index[-1].date()})"
                    )
                    continue

            # 第2优先级：本地 CSV
            df = self._load_from_csv(symbol)
            if df is not None and not df.empty:
                self._price_cache[symbol] = df
                logger.info(f"[CSV] {symbol}: {len(df)} 条记录")
                continue

            # 第3优先级：警告，回测时保留上次价格
            logger.warning(
                f"[无数据] {symbol}: akshare 和本地 CSV 均未找到数据，"
                f"回测期间将使用上次价格（或零）"
            )

    # ──────────────────────────────────────────────────────────────
    # 回测时间轴控制
    # ──────────────────────────────────────────────────────────────

    def set_date(self, date: str) -> None:
        """
        设置当前回测日期，更新所有订阅 ticker 的价格。

        ⚠️ 关键设计：只取 date 当天或之前的最近一条数据。
           不能取 date 之后的数据！否则产生"未来函数"（look-ahead bias），
           回测结果会虚高，完全失去参考价值。
        """
        self._current_date = date
        date_dt = pd.Timestamp(date)

        for symbol in self.subscribed_symbols:
            df = self._price_cache.get(symbol)
            if df is None or df.empty:
                logger.warning(f"[set_date] {symbol} 无缓存数据，跳过 {date}")
                continue

            # 精确匹配当天（最常见情况）
            if date_dt in df.index:
                row = df.loc[date_dt]
            else:
                # A股特殊情况：节假日、停牌时没有当天数据
                # → 取最近一个有数据的交易日（向前填充）
                valid_dates = df.index[df.index <= date_dt]
                if len(valid_dates) == 0:
                    logger.warning(f"[set_date] {symbol} 在 {date} 之前无数据")
                    continue
                row = df.loc[valid_dates[-1]]

            self.open_prices[symbol] = float(row["open"])
            self.close_prices[symbol] = float(row["close"])
            self.latest_prices[symbol] = float(row["open"])

            logger.debug(
                f"{symbol} @ {date}: "
                f"open={self.open_prices[symbol]:.2f}, "
                f"close={self.close_prices[symbol]:.2f}"
            )

    # ──────────────────────────────────────────────────────────────
    # 价格推送（触发 Agent 回调）
    # ──────────────────────────────────────────────────────────────

    def emit_open_prices(self) -> None:
        """
        推送开盘价给所有 callback。

        回测流程中，早盘时调用：
          → Analyst Agent 收到价格 → 发起研究 → 生成分析报告
        """
        if not self._current_date:
            return

        timestamp = int(
            datetime.strptime(self._current_date, "%Y-%m-%d").timestamp() * 1000
        )

        for symbol in self.subscribed_symbols:
            price = self.open_prices.get(symbol)
            if price is None or price <= 0:
                logger.warning(f"无效开盘价 {symbol}: {price}")
                continue

            self.latest_prices[symbol] = price
            self._emit_price(symbol, price, timestamp)

    def emit_close_prices(self) -> None:
        """
        推送收盘价给所有 callback。

        回测流程中，收盘时调用：
          → PM Agent 收到价格 → 做出买卖决策

        ⚠️ A股特殊规则（Day 3 实现）：
          T+1：当天买入的股票，明天才能卖出
          涨跌停：当天收盘价涨停时，买入订单无法成交（买不到）
        """
        if not self._current_date:
            return

        timestamp = int(
            datetime.strptime(self._current_date, "%Y-%m-%d").timestamp() * 1000
        )
        timestamp += 23400000  # +6.5小时 → 15:00收盘时间戳

        for symbol in self.subscribed_symbols:
            price = self.close_prices.get(symbol)
            if price is None or price <= 0:
                logger.warning(f"无效收盘价 {symbol}: {price}")
                continue

            self.latest_prices[symbol] = price
            self._emit_price(symbol, price, timestamp)

    def _emit_price(self, symbol: str, price: float, timestamp: int) -> None:
        """向所有注册的 callback 推送单条价格数据"""
        open_price = self.open_prices.get(symbol, price)
        close_price = self.close_prices.get(symbol, price)
        ret = ((price - open_price) / open_price * 100) if open_price > 0 else 0.0

        price_data = {
            "symbol": symbol,
            "price": price,
            "timestamp": timestamp,
            "open": open_price,
            "close": close_price,
            "high": max(open_price, close_price),
            "low": min(open_price, close_price),
            "ret": ret,
        }

        for callback in self.price_callbacks:
            try:
                callback(price_data)
            except Exception as e:
                logger.error(f"回调异常 {symbol}: {e}", exc_info=True)

    # ──────────────────────────────────────────────────────────────
    # 查询接口
    # ──────────────────────────────────────────────────────────────

    def get_price_for_date(
        self,
        symbol: str,
        date: str,
        price_type: str = "close",
    ) -> Optional[float]:
        """
        获取指定日期的历史价格（不修改当前状态）。

        用途：Agent 研究时回溯历史价格、计算涨幅等。

        Args:
            symbol:     ticker（内部格式，如 600519.SH）
            date:       日期字符串（YYYY-MM-DD）
            price_type: "open" 或 "close"（默认 close）
        """
        df = self._price_cache.get(symbol)
        if df is None or df.empty:
            return self.latest_prices.get(symbol)

        date_dt = pd.Timestamp(date)
        if date_dt in df.index:
            return float(df.loc[date_dt, price_type])

        valid_dates = df.index[df.index <= date_dt]
        if len(valid_dates) == 0:
            return self.latest_prices.get(symbol)
        return float(df.loc[valid_dates[-1], price_type])

    def get_latest_price(self, symbol: str) -> Optional[float]:
        return self.latest_prices.get(symbol)

    def get_all_latest_prices(self) -> Dict[str, float]:
        return self.latest_prices.copy()

    def get_open_price(self, symbol: str) -> Optional[float]:
        price = self.open_prices.get(symbol)
        if price is None or price <= 0:
            return self.latest_prices.get(symbol)
        return price

    def get_close_price(self, symbol: str) -> Optional[float]:
        price = self.close_prices.get(symbol)
        if price is None or price <= 0:
            return self.latest_prices.get(symbol)
        return price

    def get_prev_close_price(
        self,
        symbol: str,
        current_date: Optional[str] = None,
    ) -> Optional[float]:
        """
        获取某个交易日对应的“前一交易日收盘价”。

        业务意义：
          A股涨跌停约束不是拿“今天收盘”算，而是拿“前收”算。
          如果回测执行层拿不到前收，就会把本来买不到/卖不掉的单子误判为可成交。

        设计说明：
          - current_date 不传时，默认使用当前回测日期 self._current_date
          - 取严格早于 current_date 的最后一个有效交易日
          - 若不存在上一交易日，返回 None，让上层决定是否降级
        """
        target_date = current_date or self._current_date
        if not target_date:
            return None

        df = self._price_cache.get(symbol)
        if df is None or df.empty:
            return None

        date_dt = pd.Timestamp(target_date)
        valid_dates = df.index[df.index < date_dt]
        if len(valid_dates) == 0:
            return None

        return float(df.loc[valid_dates[-1], "close"])

    # ──────────────────────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def reset_open_prices(self) -> None:
        # 保持连续性，不清除价格
        pass

    def clear_cache(self, symbol: Optional[str] = None) -> None:
        """
        清除内存缓存（磁盘缓存保留）。
        用于需要重新拉取数据的场景。
        """
        if symbol:
            self._price_cache.pop(symbol, None)
        else:
            self._price_cache.clear()
