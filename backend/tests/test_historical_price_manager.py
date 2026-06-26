# -*- coding: utf-8 -*-
"""
Unit tests for HistoricalPriceManager (A股数据层)

运行方式：
    cd evotraders/backend
    pytest tests/test_historical_price_manager.py -v

测试策略：
  - 使用 unittest.mock 避免真实网络请求（不依赖 akshare 联网）
  - 测试核心逻辑：ticker 格式转换、数据降级链、时间轴防未来函数
"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from backend.data.historical_price_manager import (
    HistoricalPriceManager,
    _is_a_share,
    _to_akshare_symbol,
)


# ──────────────────────────────────────────────────────────────────
# 工具函数测试
# ──────────────────────────────────────────────────────────────────

class TestTickerUtils:
    """测试 ticker 格式工具函数"""

    def test_is_a_share_with_suffix(self):
        """带后缀的A股 ticker 应该被识别"""
        assert _is_a_share("600519.SH") is True
        assert _is_a_share("000001.SZ") is True
        assert _is_a_share("430047.BJ") is True

    def test_is_a_share_pure_digits(self):
        """纯6位数字也视为A股"""
        assert _is_a_share("600519") is True
        assert _is_a_share("000001") is True

    def test_is_a_share_us_stocks(self):
        """美股 ticker 不是A股"""
        assert _is_a_share("AAPL") is False
        assert _is_a_share("TSLA") is False
        assert _is_a_share("BRK.B") is False

    def test_to_akshare_symbol(self):
        """ticker 转换为 akshare 格式（去掉后缀）"""
        assert _to_akshare_symbol("600519.SH") == "600519"
        assert _to_akshare_symbol("000001.SZ") == "000001"
        assert _to_akshare_symbol("600519") == "600519"  # 已经是纯数字


# ──────────────────────────────────────────────────────────────────
# 数据加载测试（Mock akshare）
# ──────────────────────────────────────────────────────────────────

def _make_mock_df(symbol: str = "600519") -> pd.DataFrame:
    """
    生成模拟的 akshare 返回数据（中文列名，模拟真实 API）

    ⚠️ 注意：akshare 实际返回的是中文列名，
    我们的 _load_akshare() 负责将其转换为英文。
    """
    dates = pd.date_range("2024-01-02", "2024-01-05", freq="B")
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "开盘": [1700.0, 1710.0, 1720.0, 1730.0],
            "收盘": [1710.0, 1720.0, 1730.0, 1740.0],
            "最高": [1715.0, 1725.0, 1735.0, 1745.0],
            "最低": [1695.0, 1705.0, 1715.0, 1725.0],
            "成交量": [10000, 12000, 11000, 13000],
            "涨跌幅": [0.5, 0.6, 0.6, 0.6],
        }
    )


class TestPreloadData:
    """测试 preload_data 的三级降级链"""

    # 强制 _price_cache_only=False，使本用例不受外部 .env(A_SHARE_PRICE_CACHE_ONLY=1) 影响——
    # 否则缓存只读模式会跳过磁盘写入，导致 mock_save 断言在全套运行时失败(测试隔离)。
    @patch("backend.data.historical_price_manager._price_cache_only", return_value=False)
    @patch("backend.data.historical_price_manager._load_akshare")
    @patch("backend.data.historical_price_manager._load_from_disk_cache", return_value=None)
    @patch("backend.data.historical_price_manager._save_to_disk_cache")
    def test_akshare_success(
        self,
        mock_save,
        mock_disk_cache,
        mock_akshare,
        mock_cache_only,
    ):
        """
        场景：akshare 拉取成功
        期望：数据进入 _price_cache，磁盘缓存写入
        """
        # 准备 mock 数据（模拟 _load_akshare 已处理好列名的输出）
        mock_df = pd.DataFrame(
            {
                "time": ["2024-01-02", "2024-01-03"],
                "open": [1700.0, 1710.0],
                "close": [1710.0, 1720.0],
                "high": [1715.0, 1725.0],
                "low": [1695.0, 1705.0],
            },
            index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
        )
        mock_akshare.return_value = mock_df

        mgr = HistoricalPriceManager(use_akshare=True)
        mgr.subscribe(["600519.SH"])
        mgr.preload_data("2024-01-01", "2024-01-31")

        assert "600519.SH" in mgr._price_cache
        assert len(mgr._price_cache["600519.SH"]) == 2
        mock_save.assert_called_once()  # 验证写入了磁盘缓存

    @patch("backend.data.historical_price_manager._load_akshare", return_value=None)
    @patch("backend.data.historical_price_manager._load_from_disk_cache", return_value=None)
    def test_fallback_to_csv(self, mock_disk_cache, mock_akshare, tmp_path):
        """
        场景：akshare 失败，本地有 CSV
        期望：降级到 CSV，数据正常加载

        ⚠️ 测试技巧：用 monkeypatch 替换 _DATA_DIR 为临时目录，
        避免污染真实数据目录。
        """
        # 在临时目录创建测试 CSV（格式与真实 CSV 一致）
        csv_content = "open,close,high,low,volume,time,ret\n1700,1710,1715,1695,10000,2024-01-02,0.5\n"
        csv_file = tmp_path / "600519_SH.csv"
        csv_file.write_text(csv_content)

        import backend.data.historical_price_manager as hpm_module
        original_dir = hpm_module._DATA_DIR
        hpm_module._DATA_DIR = tmp_path

        try:
            mgr = HistoricalPriceManager(use_akshare=True)
            mgr.subscribe(["600519.SH"])
            mgr.preload_data("2024-01-01", "2024-01-31")

            assert "600519.SH" in mgr._price_cache
        finally:
            hpm_module._DATA_DIR = original_dir  # 恢复原路径

    @patch("backend.data.historical_price_manager._load_akshare", return_value=None)
    @patch("backend.data.historical_price_manager._load_from_disk_cache", return_value=None)
    def test_no_data_graceful(self, mock_disk_cache, mock_akshare):
        """
        场景：akshare 失败，CSV 也没有
        期望：不崩溃，记录警告，_price_cache 里没有该 symbol
        """
        mgr = HistoricalPriceManager(use_akshare=True)
        mgr.subscribe(["999999.SH"])  # 不存在的股票
        mgr.preload_data("2024-01-01", "2024-01-31")

        assert "999999.SH" not in mgr._price_cache  # 无数据但不崩溃

    def test_reuse_superset_parquet_cache(self, tmp_path):
        """
        场景：本地缓存覆盖更大日期区间
        期望：短区间回测可直接复用并切片，不再要求缓存文件名完全一致
        """
        import backend.data.historical_price_manager as hpm_module

        original_cache_dir = hpm_module._CACHE_DIR
        hpm_module._CACHE_DIR = tmp_path

        try:
            dates = pd.to_datetime(
                ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]
            )
            df = pd.DataFrame(
                {
                    "time": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
                    "open": [100.0, 101.0, 102.0, 103.0],
                    "close": [101.0, 102.0, 103.0, 104.0],
                    "high": [101.5, 102.5, 103.5, 104.5],
                    "low": [99.5, 100.5, 101.5, 102.5],
                    "volume": [1, 2, 3, 4],
                    "ret": [0.1, 0.2, 0.3, 0.4],
                },
                index=dates,
            )
            df.index.name = "Date"

            cache_file = tmp_path / "600519_SH_2024-01-01_2024-03-31.parquet"
            df.to_parquet(cache_file)

            mgr = HistoricalPriceManager(use_akshare=True)
            mgr.subscribe(["600519.SH"])
            mgr.preload_data("2024-01-02", "2024-01-03")

            assert "600519.SH" in mgr._price_cache
            cached_df = mgr._price_cache["600519.SH"]
            assert len(cached_df) == 2
            assert cached_df.index[0] == pd.Timestamp("2024-01-02")
            assert cached_df.index[-1] == pd.Timestamp("2024-01-03")
        finally:
            hpm_module._CACHE_DIR = original_cache_dir

    def test_us_stock_skips_akshare(self):
        """
        场景：订阅美股 ticker（AAPL）
        期望：不走 akshare，只走 CSV
        """
        mgr = HistoricalPriceManager(use_akshare=True)
        mgr.subscribe(["AAPL"])

        # AAPL 不是A股，_is_a_share 返回 False → 不调用 akshare
        with patch("backend.data.historical_price_manager._load_akshare") as mock_ak:
            mgr.preload_data("2024-01-01", "2024-01-31")
            mock_ak.assert_not_called()


# ──────────────────────────────────────────────────────────────────
# 回测时间轴测试（防未来函数）
# ──────────────────────────────────────────────────────────────────

class TestSetDate:
    """测试 set_date 的时间轴防未来函数逻辑"""

    def _make_manager_with_data(self) -> HistoricalPriceManager:
        """创建一个预置了数据的 manager（不走网络）"""
        mgr = HistoricalPriceManager(use_akshare=False)
        mgr.subscribe(["600519.SH"])

        # 直接注入模拟数据到缓存
        dates = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
        df = pd.DataFrame(
            {
                "open": [1700.0, 1710.0, 1720.0],
                "close": [1710.0, 1720.0, 1730.0],
            },
            index=dates,
        )
        mgr._price_cache["600519.SH"] = df
        return mgr

    def test_exact_date_match(self):
        """精确匹配交易日"""
        mgr = self._make_manager_with_data()
        mgr.set_date("2024-01-03")

        assert mgr.open_prices["600519.SH"] == 1710.0
        assert mgr.close_prices["600519.SH"] == 1720.0

    def test_holiday_forward_fill(self):
        """
        节假日/停牌日（2024-01-01是元旦，没有数据）
        期望：使用最近一个有数据的交易日（向前填充，不是向后！）

        这是防未来函数的关键：只能用"过去"的数据。
        """
        mgr = self._make_manager_with_data()
        # 2024-01-01 没有数据，最近的数据是 2024-01-02
        mgr.set_date("2024-01-01")

        # 1月1日之前没有数据，所以 set_date 应该跳过（不设置价格）
        assert "600519.SH" not in mgr.open_prices

    def test_no_future_data(self):
        """
        ⚠️ 核心防未来函数测试
        在 2024-01-03 设置日期，不应该看到 2024-01-04 的价格
        """
        mgr = self._make_manager_with_data()
        mgr.set_date("2024-01-03")

        # 只能看到 01-03 的数据，不能是 01-04 的 1720/1730
        assert mgr.open_prices["600519.SH"] == 1710.0  # 01-03 开盘
        assert mgr.close_prices["600519.SH"] == 1720.0  # 01-03 收盘
        assert mgr.open_prices["600519.SH"] != 1720.0  # 不是 01-04 的开盘

    def test_prev_close_uses_previous_trading_day(self):
        """
        执行约束需要的是“前收”，不是当天收盘。
        2024-01-03 的前收应该来自 2024-01-02 的 close。
        """
        mgr = self._make_manager_with_data()
        mgr.set_date("2024-01-03")

        prev_close = mgr.get_prev_close_price("600519.SH")
        assert prev_close == 1710.0

    def test_prev_close_missing_for_first_trading_day(self):
        """首个交易日没有更早数据时，应返回 None 让上层自行降级。"""
        mgr = self._make_manager_with_data()
        mgr.set_date("2024-01-02")

        prev_close = mgr.get_prev_close_price("600519.SH")
        assert prev_close is None


# ──────────────────────────────────────────────────────────────────
# 价格推送测试
# ──────────────────────────────────────────────────────────────────

class TestEmitPrices:
    """测试价格推送回调机制"""

    def test_emit_triggers_callback(self):
        """确认 emit_open_prices 触发了注册的 callback"""
        received = []

        def my_callback(price_data):
            received.append(price_data)

        mgr = HistoricalPriceManager(use_akshare=False)
        mgr.subscribe(["600519.SH"])
        mgr.add_price_callback(my_callback)

        # 直接设置价格（跳过 preload）
        mgr._current_date = "2024-01-03"
        mgr.open_prices["600519.SH"] = 1710.0
        mgr.close_prices["600519.SH"] = 1720.0

        mgr.emit_open_prices()

        assert len(received) == 1
        assert received[0]["symbol"] == "600519.SH"
        assert received[0]["price"] == 1710.0
        assert received[0]["open"] == 1710.0

    def test_callback_exception_does_not_crash(self):
        """
        ⚠️ 鲁棒性测试：某个 callback 抛异常不应该中断其他 callback

        实际场景：Agent 处理价格数据时崩溃，不能影响整个回测流程。
        """
        results = []

        def bad_callback(data):
            raise RuntimeError("Agent 崩了")

        def good_callback(data):
            results.append(data["symbol"])

        mgr = HistoricalPriceManager(use_akshare=False)
        mgr.subscribe(["600519.SH"])
        mgr.add_price_callback(bad_callback)
        mgr.add_price_callback(good_callback)

        mgr._current_date = "2024-01-03"
        mgr.open_prices["600519.SH"] = 1710.0
        mgr.close_prices["600519.SH"] = 1720.0

        mgr.emit_open_prices()  # bad_callback 抛异常，但不崩溃

        assert "600519.SH" in results  # good_callback 依然执行了


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
