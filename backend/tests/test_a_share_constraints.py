# -*- coding: utf-8 -*-
"""
A股交易约束引擎单元测试

运行方式：
    cd evotraders/backend
    pytest tests/test_a_share_constraints.py -v

测试策略：
  每个测试用例对应一个真实A股交易场景，
  注释里说明"为什么这个场景很重要"。
"""
import pytest
from backend.utils.a_share_constraints import (
    AShareConstraints,
    ASharePortfolioTradeExecutor,
    calc_transaction_cost,
    calc_limit_up_price,
    calc_limit_down_price,
    round_to_lot,
    is_st_stock,
)


# ──────────────────────────────────────────────────────────────────
# 工具函数测试
# ──────────────────────────────────────────────────────────────────

class TestLimitPriceCalc:
    """涨跌停价格计算测试"""

    def test_normal_stock_limit_up(self):
        """普通股涨停价 = 前收盘 × 1.10"""
        price = calc_limit_up_price("600519.SH", prev_close=1000.0)
        assert price == pytest.approx(1100.0, abs=0.01)

    def test_normal_stock_limit_down(self):
        """普通股跌停价 = 前收盘 × 0.90"""
        price = calc_limit_down_price("600519.SH", prev_close=1000.0)
        assert price == pytest.approx(900.0, abs=0.01)

    def test_st_stock_limit_up(self):
        """ST 股涨停幅度只有 5%"""
        price = calc_limit_up_price("ST某股", prev_close=10.0)
        assert price == pytest.approx(10.5, abs=0.01)

    def test_st_stock_limit_down(self):
        """ST 股跌停幅度只有 5%"""
        price = calc_limit_down_price("ST某股", prev_close=10.0)
        assert price == pytest.approx(9.5, abs=0.01)


class TestTransactionCost:
    """交易费用计算测试"""

    def test_buy_has_no_stamp_duty(self):
        """买入不收印花税"""
        cost = calc_transaction_cost("buy", 100, 100.0, "600519.SH")
        assert cost["stamp_duty"] == 0.0

    def test_sell_has_stamp_duty(self):
        """卖出收 0.1% 印花税"""
        cost = calc_transaction_cost("sell", 100, 100.0, "600519.SH")
        assert cost["stamp_duty"] == pytest.approx(10.0, abs=0.01)  # 10000 × 0.1%

    def test_shanghai_stock_has_transfer_fee(self):
        """沪市股票有过户费（代码以6开头）"""
        cost = calc_transaction_cost("buy", 100, 100.0, "600519.SH")
        assert cost["transfer_fee"] > 0

    def test_shenzhen_stock_no_transfer_fee(self):
        """深市股票无过户费（代码以0或3开头）"""
        cost = calc_transaction_cost("buy", 100, 100.0, "000001.SZ")
        assert cost["transfer_fee"] == 0.0

    def test_minimum_commission(self):
        """最低佣金 5 元（买入 1 股便宜股时会触发）"""
        cost = calc_transaction_cost("buy", 1, 1.0, "000001.SZ")  # 1元股票买1股
        assert cost["commission"] == pytest.approx(5.0)  # 最低佣金保底

    def test_lot_size_rounding(self):
        """手数取整：只取100股整数倍，向下取"""
        assert round_to_lot(150) == 100
        assert round_to_lot(99) == 0
        assert round_to_lot(250) == 200
        assert round_to_lot(100) == 100


# ──────────────────────────────────────────────────────────────────
# 涨跌停约束测试
# ──────────────────────────────────────────────────────────────────

class TestLimitConstraints:
    """涨跌停板拦截测试"""

    def test_buy_blocked_at_limit_up(self):
        """
        场景：茅台今天涨停（前收盘 1000 元，涨停价 1100 元）
        期望：买入被拒绝
        现实意义：涨停时大量人挂单排队，实际上很难成交
        """
        result = AShareConstraints.validate_buy_order(
            symbol="600519.SH",
            quantity=100,
            price=1100.0,      # 刚好涨停价
            prev_close=1000.0,
            available_cash=200000.0,
        )
        assert result.approved is False
        assert "涨停" in result.rejection_reason

    def test_sell_blocked_at_limit_down(self):
        """
        场景：某股今天跌停（前收盘 10 元，跌停价 9 元）
        期望：卖出被拒绝
        现实意义：跌停时买方撤单，根本没人接手，"想卖卖不出去"
        """
        result = AShareConstraints.validate_sell_order(
            symbol="000001.SZ",
            quantity=100,
            price=9.0,          # 刚好跌停价
            prev_close=10.0,
            available_shares=100,
        )
        assert result.approved is False
        assert "跌停" in result.rejection_reason

    def test_normal_price_buy_allowed(self):
        """正常价格范围内，买入不受影响"""
        result = AShareConstraints.validate_buy_order(
            symbol="600519.SH",
            quantity=100,
            price=1050.0,      # 涨幅 5%，未触板
            prev_close=1000.0,
            available_cash=200000.0,
        )
        assert result.approved is True


# ──────────────────────────────────────────────────────────────────
# T+1 制度测试（最核心的A股特殊规则）
# ──────────────────────────────────────────────────────────────────

class TestT1Rule:
    """T+1 交易规则测试"""

    def _make_executor(self) -> ASharePortfolioTradeExecutor:
        return ASharePortfolioTradeExecutor(
            initial_portfolio={
                "cash": 500000.0,
                "positions": {},
                "margin_requirement": 0.0,
                "margin_used": 0.0,
            }
        )

    def test_cannot_sell_on_same_day_as_buy(self):
        """
        场景：今天买了茅台 100 股，当天就想卖
        期望：卖出被 T+1 规则拒绝
        现实意义：这是A股和美股最大的制度差异之一
        """
        executor = self._make_executor()
        executor.set_date("2024-01-02")
        executor._prev_closes["600519.SH"] = 1000.0

        # 买入 100 股
        buy_result = executor._buy_long_position("600519.SH", 100, 1050.0, "2024-01-02")
        assert buy_result["status"] == "success"

        # 当天立刻尝试卖出（T+1 应该拒绝）
        available = executor.get_available_shares("600519.SH")
        assert available == 0  # 今天买的，今天不能卖

        sell_result = executor._sell_long_position("600519.SH", 100, 1060.0, "2024-01-02")
        assert sell_result["status"] == "failed"
        assert "T+1" in sell_result["reason"]

    def test_can_sell_next_trading_day(self):
        """
        场景：今天（1/2）买了茅台，明天（1/3）能卖
        期望：次日卖出成功
        """
        executor = self._make_executor()
        executor.set_date("2024-01-02")
        executor._prev_closes["600519.SH"] = 1000.0

        # 1/2 买入
        executor._buy_long_position("600519.SH", 100, 1050.0, "2024-01-02")

        # 推进到 1/3（模拟回测日期前进）
        executor.set_date("2024-01-03")
        executor._prev_closes["600519.SH"] = 1050.0

        # 1/3 应该可以卖了
        available = executor.get_available_shares("600519.SH")
        assert available == 100  # 昨天买的今天可以卖

        sell_result = executor._sell_long_position("600519.SH", 100, 1080.0, "2024-01-03")
        assert sell_result["status"] == "success"

    def test_partial_sellable_position(self):
        """
        场景：已有 200 股（昨天买的），今天又买了 100 股
        期望：今天只能卖昨天的 200 股，今天买的 100 股锁仓

        这模拟了"加仓当天不能卖当天加的部分"的规则
        """
        executor = self._make_executor()

        # 1/2：买入 200 股（老仓位）
        executor.set_date("2024-01-02")
        executor._prev_closes["600519.SH"] = 1000.0
        executor._buy_long_position("600519.SH", 200, 1010.0, "2024-01-02")

        # 1/3：再买 100 股（新仓位）
        executor.set_date("2024-01-03")
        executor._prev_closes["600519.SH"] = 1010.0
        executor._buy_long_position("600519.SH", 100, 1020.0, "2024-01-03")

        # 1/3 当天：只有 200 股可卖（今天加的 100 股锁仓）
        available = executor.get_available_shares("600519.SH")
        assert available == 200

    def test_short_selling_blocked(self):
        """
        场景：PM Agent 给出做空信号
        期望：A股版本直接拒绝，不执行做空
        """
        executor = self._make_executor()
        executor.set_date("2024-01-02")

        result = executor._open_short_position("600519.SH", 100, 1000.0, "2024-01-02")
        assert result["status"] == "failed"
        assert "做空" in result["reason"]


# ──────────────────────────────────────────────────────────────────
# 手续费测试
# ──────────────────────────────────────────────────────────────────

class TestTransactionCostDeduction:
    """验证手续费从 cash 中正确扣除"""

    def test_buy_deducts_commission_from_cash(self):
        """买入后，现金应该减少（股票价值 + 手续费）"""
        executor = ASharePortfolioTradeExecutor(
            initial_portfolio={
                "cash": 200000.0,
                "positions": {},
                "margin_requirement": 0.0,
                "margin_used": 0.0,
            }
        )
        executor.set_date("2024-01-02")
        executor._prev_closes["600519.SH"] = 1000.0

        initial_cash = executor.portfolio["cash"]
        executor._buy_long_position("600519.SH", 100, 1050.0, "2024-01-02")

        trade_value = 100 * 1050.0  # 105,000
        expected_commission = max(trade_value * 0.0003, 5.0)  # 31.5 元
        # ⚠️ 注意：600519.SH 是沪市股票，买入还要收过户费 0.002%
        # 漏掉这一项会导致"回测比实盘多赚一点点"的系统性偏差
        expected_transfer_fee = trade_value * 0.00002  # 2.1 元
        expected_cash = initial_cash - trade_value - expected_commission - expected_transfer_fee

        assert executor.portfolio["cash"] == pytest.approx(expected_cash, abs=0.1)

    def test_sell_deducts_stamp_duty(self):
        """卖出后，印花税从收益中扣除"""
        executor = ASharePortfolioTradeExecutor(
            initial_portfolio={
                "cash": 0.0,
                "positions": {
                    "600519.SH": {
                        "long": 100,
                        "short": 0,
                        "long_cost_basis": 1000.0,
                        "short_cost_basis": 0.0,
                    }
                },
                "margin_requirement": 0.0,
                "margin_used": 0.0,
            }
        )
        executor.set_date("2024-01-02")
        executor._prev_closes["600519.SH"] = 1000.0

        # 直接注入持仓（跳过买入，模拟昨天已经买好了）
        # 为了让卖出通过 T+1，这 100 股不在今天的锁仓里
        executor._sell_long_position("600519.SH", 100, 1100.0, "2024-01-02")

        trade_value = 100 * 1100.0  # 110,000
        stamp_duty = trade_value * 0.001  # 110 元
        commission = max(trade_value * 0.0003, 5.0)  # 33 元
        transfer_fee = trade_value * 0.00002  # 2.2 元（沪市）
        expected_cash = trade_value - stamp_duty - commission - transfer_fee

        assert executor.portfolio["cash"] == pytest.approx(expected_cash, abs=1.0)


# ──────────────────────────────────────────────────────────────────
# 约束日志测试
# ──────────────────────────────────────────────────────────────────

class TestConstraintLog:
    """约束拦截日志测试"""

    def test_blocked_orders_are_logged(self):
        """被拦截的订单应该记录在日志里（用于回测复盘）"""
        executor = ASharePortfolioTradeExecutor(
            initial_portfolio={
                "cash": 500000.0,
                "positions": {},
                "margin_requirement": 0.0,
                "margin_used": 0.0,
            }
        )
        executor.set_date("2024-01-02")
        executor._prev_closes["600519.SH"] = 1000.0

        # 触发涨停拦截
        executor._buy_long_position("600519.SH", 100, 1100.0, "2024-01-02")

        summary = executor.get_constraint_summary()
        assert summary["blocked_orders"] == 1
        assert summary["blocked_by_type"]["limit_up"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
