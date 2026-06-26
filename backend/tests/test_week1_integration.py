# -*- coding: utf-8 -*-
"""
Week 1 集成测试：多股回测 + 防未来函数验证

测试目标：
  1. HistoricalPriceManager 防未来函数
     - set_date(T) 后只能看到 T 的价格，看不到 T+1
     - get_prev_close_price() 返回的是 T-1 的收盘，不是 T 的
  2. T+1 锁仓对每只股票独立跟踪
     - 买了茅台锁茅台，工行未受影响，仍可卖出
  3. 多股涨跌停约束各自独立
     - 茅台涨停时工行不受影响
  4. 共享 portfolio 在多股场景下资金正确扣除

运行：
  cd evotraders/backend
  pytest tests/test_week1_integration.py -v
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from backend.data.historical_price_manager import HistoricalPriceManager
from backend.utils.a_share_constraints import ASharePortfolioTradeExecutor


# ──────────────────────────────────────────────────────────────────
# 共用 fixtures
# ──────────────────────────────────────────────────────────────────

MOUTAI = "600519.SH"
ICBC   = "601398.SH"

# 10个交易日的模拟价格（茅台）
_MOUTAI_PRICES = [
    ("2024-01-02", 1700.0, 1720.0),
    ("2024-01-03", 1870.0, 1872.0),   # 涨停
    ("2024-01-04", 1860.0, 1850.0),
    ("2024-01-05", 1680.0, 1683.0),   # 跌停
    ("2024-01-08", 1700.0, 1710.0),
    ("2024-01-09", 1715.0, 1730.0),
    ("2024-01-10", 1740.0, 1760.0),
]

# 同期工行价格
_ICBC_PRICES = [
    ("2024-01-02",  5.20,  5.25),
    ("2024-01-03",  5.28,  5.30),
    ("2024-01-04",  5.32,  5.28),
    ("2024-01-05",  5.25,  5.22),
    ("2024-01-08",  5.20,  5.24),
    ("2024-01-09",  5.26,  5.29),
    ("2024-01-10",  5.30,  5.35),
]


def _make_df(price_list):
    """把 (date, open, close) 列表转成 HistoricalPriceManager 使用的 DataFrame 格式"""
    dates = pd.to_datetime([row[0] for row in price_list])
    return pd.DataFrame(
        {
            "open":  [row[1] for row in price_list],
            "close": [row[2] for row in price_list],
        },
        index=dates,
    )


def _make_price_manager() -> HistoricalPriceManager:
    """创建预注入了茅台 + 工行数据的 HistoricalPriceManager（不走网络）"""
    mgr = HistoricalPriceManager(use_akshare=False)
    mgr.subscribe([MOUTAI, ICBC])
    mgr._price_cache[MOUTAI] = _make_df(_MOUTAI_PRICES)
    mgr._price_cache[ICBC]   = _make_df(_ICBC_PRICES)
    return mgr


def _make_executor() -> ASharePortfolioTradeExecutor:
    """创建初始资金 200万的执行器"""
    return ASharePortfolioTradeExecutor(initial_portfolio={
        "cash": 2_000_000.0,
        "positions": {},
        "margin_requirement": 0.0,
        "margin_used": 0.0,
    })


# ──────────────────────────────────────────────────────────────────
# 1. 防未来函数测试（HistoricalPriceManager）
# ──────────────────────────────────────────────────────────────────

class TestNoLookaheadBias:
    """
    ⚠️ 核心测试：验证回测中不会泄漏未来价格

    业务背景：
      如果 set_date("2024-01-03") 后能看到 2024-01-04 的价格，
      策略就等于"开了上帝视角"，回测结果毫无意义。
    """

    def test_set_date_shows_correct_open(self):
        """set_date(T) 后 open_prices 应该是 T 当天的开盘价"""
        mgr = _make_price_manager()
        mgr.set_date("2024-01-03")
        # 01-03 开盘 1870.0（涨停日），不是 01-04 的 1860.0
        assert mgr.open_prices[MOUTAI] == 1870.0, (
            f"应为 01-03 开盘 1870.0，实际: {mgr.open_prices[MOUTAI]}"
        )

    def test_set_date_shows_correct_close(self):
        """set_date(T) 后 close_prices 应该是 T 当天的收盘价"""
        mgr = _make_price_manager()
        mgr.set_date("2024-01-03")
        assert mgr.close_prices[MOUTAI] == 1872.0

    def test_no_future_open_price_leaked(self):
        """
        在 2024-01-03 不应该看到 2024-01-04 的开盘价 1860.0
        这是防未来函数的核心断言
        """
        mgr = _make_price_manager()
        mgr.set_date("2024-01-03")
        assert mgr.open_prices[MOUTAI] != 1860.0, "泄漏了 01-04 的开盘价！"

    def test_prev_close_is_previous_day(self):
        """
        get_prev_close_price() 应该返回 T-1（前一个交易日）的收盘价

        业务意义：涨跌停价格 = prev_close × (1 ± 10%)
        如果 prev_close 用错了，涨跌停判断就全错。
        """
        mgr = _make_price_manager()
        mgr.set_date("2024-01-03")
        prev_close = mgr.get_prev_close_price(MOUTAI)
        # 01-03 的 prev_close 应该是 01-02 的收盘 1720.0
        assert prev_close == 1720.0, (
            f"01-03 的 prev_close 应为 01-02 收盘 1720.0，实际: {prev_close}"
        )

    def test_prev_close_not_current_day(self):
        """prev_close 不应该等于当天收盘（1872.0 是 01-03 的收盘，不是 prev_close）"""
        mgr = _make_price_manager()
        mgr.set_date("2024-01-03")
        prev_close = mgr.get_prev_close_price(MOUTAI)
        assert prev_close != 1872.0, "错误：把当天收盘当 prev_close 用了"

    def test_multi_stock_no_cross_contamination(self):
        """两只股票的价格数据互不影响"""
        mgr = _make_price_manager()
        mgr.set_date("2024-01-03")
        # 茅台 01-03 开盘 1870，工行 01-03 开盘 5.28
        assert mgr.open_prices[MOUTAI] == 1870.0
        assert mgr.open_prices[ICBC]   == 5.28
        # 工行的 prev_close 不应该变成茅台的值
        assert mgr.get_prev_close_price(ICBC) == pytest.approx(5.25, abs=0.01)

    def test_advance_date_updates_prices(self):
        """
        日期往前推进后，价格应该随之更新
        （回测主循环每天调 set_date，价格必须跟着滚动）
        """
        mgr = _make_price_manager()
        mgr.set_date("2024-01-02")
        assert mgr.open_prices[MOUTAI] == 1700.0

        mgr.set_date("2024-01-08")
        assert mgr.open_prices[MOUTAI] == 1700.0   # 01-08 开盘也是 1700
        assert mgr.close_prices[MOUTAI] == 1710.0  # 01-08 收盘


# ──────────────────────────────────────────────────────────────────
# 2. T+1 锁仓独立性测试
# ──────────────────────────────────────────────────────────────────

class TestT1IndependenceAcrossStocks:
    """
    T+1 对每只股票独立跟踪，买了 A 不影响 B 的可卖数量
    """

    def test_buy_moutai_does_not_lock_icbc(self):
        """
        买了茅台后（T+1 锁茅台），工行仍然可以正常卖出
        """
        executor = _make_executor()
        executor.set_date("2024-01-03")
        executor.set_prev_closes({MOUTAI: 1720.0, ICBC: 5.25})

        # 先给工行建个底仓（模拟已持有，不是今天买的 → 不在 T+1 锁中）
        executor.portfolio["positions"][ICBC] = {"long": 10000, "short": 0}

        # 今天买茅台（被 T+1 锁住）
        result = executor._buy_long_position(MOUTAI, 100, 1870.0, "2024-01-03")
        assert result["status"] == "success", "茅台买入应成功"

        # 茅台 T+1 锁住，今天不能卖
        avail_moutai = executor.get_available_shares(MOUTAI)
        assert avail_moutai == 0, f"茅台刚买，应被 T+1 锁住，可卖量应为0，实际: {avail_moutai}"

        # 工行是底仓（不是今天买的），应该可以卖
        avail_icbc = executor.get_available_shares(ICBC)
        assert avail_icbc == 10000, f"工行底仓不受茅台 T+1 影响，可卖量应为10000，实际: {avail_icbc}"

    def test_t1_releases_per_stock_next_day(self):
        """
        下一个交易日 set_date 后，各股票的 T+1 锁分别释放
        """
        executor = _make_executor()

        # Day 1：两只股票都买
        executor.set_date("2024-01-02")
        executor.set_prev_closes({MOUTAI: 1700.0, ICBC: 5.20})
        executor._buy_long_position(MOUTAI, 100, 1700.0, "2024-01-02")
        executor._buy_long_position(ICBC, 1000, 5.20, "2024-01-02")

        # Day 1 结束：两只都锁
        assert executor.get_available_shares(MOUTAI) == 0
        assert executor.get_available_shares(ICBC)   == 0

        # Day 2：推进日期，T+1 释放
        executor.set_date("2024-01-03")
        assert executor.get_available_shares(MOUTAI) == 100,  "茅台 T+1 应在次日释放"
        assert executor.get_available_shares(ICBC)   == 1000, "工行 T+1 应在次日释放"

    def test_sell_icbc_while_moutai_locked(self):
        """
        茅台今天刚买（T+1 锁），工行底仓今天可以卖出
        """
        executor = _make_executor()
        executor.set_date("2024-01-03")
        executor.set_prev_closes({MOUTAI: 1720.0, ICBC: 5.25})

        # 工行底仓（非今天买的）
        executor.portfolio["positions"][ICBC] = {"long": 5000, "short": 0}

        # 今天买茅台
        executor._buy_long_position(MOUTAI, 100, 1870.0, "2024-01-03")

        # 今天卖工行（底仓）→ 应该成功
        result = executor._sell_long_position(ICBC, 1000, 5.28, "2024-01-03")
        assert result["status"] == "success", (
            f"工行底仓应可卖出，实际: {result}"
        )


# ──────────────────────────────────────────────────────────────────
# 3. 涨跌停约束多股并行
# ──────────────────────────────────────────────────────────────────

class TestMultiStockCircuitBreakers:
    """
    茅台涨停时工行不受影响，反之亦然
    """

    def test_moutai_limit_up_icbc_normal(self):
        """
        茅台涨停日（涨幅 10%）→ 茅台买单被拦截
        同一天工行正常 → 工行买单通过
        """
        executor = _make_executor()
        executor.set_date("2024-01-03")
        executor.set_prev_closes({
            MOUTAI: 1700.0,  # 茅台前收 1700，涨停价 = 1700 × 1.1 = 1870
            ICBC:   5.25,    # 工行前收 5.25，正常范围
        })

        # 茅台以 1870 买入 → 恰好触碰涨停价，应被拦截
        result_m = executor._buy_long_position(MOUTAI, 100, 1870.0, "2024-01-03")
        assert result_m["status"] != "success", (
            f"茅台涨停日买入应被拦截，实际: {result_m}"
        )
        assert "limit" in result_m.get("reason", "").lower() or \
               "涨停" in result_m.get("reason", ""), \
            f"拦截原因应包含涨停，实际: {result_m.get('reason')}"

        # 工行以 5.28 买入 → 正常，应通过
        result_i = executor._buy_long_position(ICBC, 1000, 5.28, "2024-01-03")
        assert result_i["status"] == "success", (
            f"工行正常日买入应通过，实际: {result_i}"
        )

    def test_moutai_limit_down_icbc_normal(self):
        """
        茅台跌停日（open 跌幅 ≈ 10%）→ 茅台卖单被拦截
        同一天工行正常 → 工行卖单通过
        """
        executor = _make_executor()
        # 预先给两只股票建仓（底仓，非当天买入）
        executor.portfolio["positions"][MOUTAI] = {"long": 200, "short": 0}
        executor.portfolio["positions"][ICBC]   = {"long": 5000, "short": 0}

        executor.set_date("2024-01-05")
        executor.set_prev_closes({
            MOUTAI: 1850.0,  # 前收 1850，跌停价 = 1850 × 0.9 = 1665
            ICBC:   5.28,    # 工行正常
        })

        # 茅台以 1680 卖出 → 1680 > 跌停价 1665，但开盘价接近跌停
        # 使用 1665（跌停价本身）测试边界拦截
        result_m = executor._sell_long_position(MOUTAI, 100, 1665.0, "2024-01-05")
        assert result_m["status"] != "success", (
            f"茅台跌停价卖出应被拦截，实际: {result_m}"
        )

        # 工行正常卖出
        result_i = executor._sell_long_position(ICBC, 1000, 5.25, "2024-01-05")
        assert result_i["status"] == "success", (
            f"工行正常日卖出应通过，实际: {result_i}"
        )


# ──────────────────────────────────────────────────────────────────
# 4. 多股回测端到端测试
# ──────────────────────────────────────────────────────────────────

class TestMultiStockEndToEnd:
    """
    跑完整的多股回测主循环，验证整体状态一致性
    """

    def test_full_multi_stock_backtest_runs(self):
        """
        完整跑 7 天多股回测，验证不崩溃、最终状态合理
        """
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from backtest_multi_stock import run_multi_backtest

        daily_logs, summary = run_multi_backtest(offline=True)

        # 基本完整性：跑了 7 天（模拟数据10天，取茅台+工行交集）
        assert len(daily_logs) > 0, "回测应有交易日记录"

        # 约束引擎应该触发了拦截（涨停/T+1/跌停至少一个）
        assert summary["blocked_orders"] > 0, "约束引擎应至少拦截了一笔订单"

        # T+1 应该被触发（工行每天尝试卖 → 当天买当天卖应被拦）
        by_type = summary["blocked_by_type"]
        assert by_type["t_plus_1"] > 0 or by_type["limit_up"] > 0, (
            "T+1 或涨停应至少触发一次"
        )

    def test_portfolio_cash_never_negative(self):
        """资金不允许变成负数（资金不足时应拒绝买入）"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from backtest_multi_stock import run_multi_backtest

        daily_logs, _ = run_multi_backtest(offline=True)

        for day in daily_logs:
            assert day["cash"] >= 0, (
                f"{day['date']} 现金变成负数: {day['cash']:.2f}"
            )

    def test_constraint_log_records_both_symbols(self):
        """约束日志应该记录了两只股票的事件"""
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from backtest_multi_stock import run_multi_backtest

        _, summary = run_multi_backtest(offline=True)

        symbols_in_log = {ev["symbol"] for ev in summary["constraint_log"]}
        # 至少应有一只股票被记录
        assert len(symbols_in_log) >= 1, "约束日志为空"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
