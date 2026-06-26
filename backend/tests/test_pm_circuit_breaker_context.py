# -*- coding: utf-8 -*-
"""
Day 10-11 集成测试：PM Agent 感知涨跌停 + T+1 可卖量

测试覆盖：
  1. build_circuit_status() — 正常/涨停/跌停/未知 状态判断
  2. PMAgent._make_decision() — 涨停拦截 long / 跌停拦截 short
  3. PMAgent._make_decision() — T+1 截断卖出量
  4. PMAgent._make_decision() — 正常决策时涨跌停信息出现在 next_hint 中
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ──────────────────────────────────────────────────────────────────
# 1. build_circuit_status() 单元测试
# ──────────────────────────────────────────────────────────────────

class TestBuildCircuitStatus:
    """验证涨跌停状态计算逻辑"""

    def _build(self, tickers, prices, prev_closes):
        from backend.utils.a_share_constraints import build_circuit_status
        return build_circuit_status(tickers, prices, prev_closes)

    def test_normal_status(self):
        """正常价格：status=normal，can_buy=True，can_sell=True"""
        result = self._build(
            ["600519.SH"],
            {"600519.SH": 1700.0},
            {"600519.SH": 1600.0},  # 涨停价=1760，跌停价=1440
        )
        info = result["600519.SH"]
        assert info["status"] == "normal"
        assert info["can_buy"] is True
        assert info["can_sell"] is True
        assert info["warning"] == ""

    def test_limit_up_status(self):
        """价格 >= 涨停价：status=limit_up，can_buy=False"""
        prev = 100.0
        limit_up = round(prev * 1.10, 2)  # 110.0
        result = self._build(
            ["000001.SZ"],
            {"000001.SZ": limit_up},
            {"000001.SZ": prev},
        )
        info = result["000001.SZ"]
        assert info["status"] == "limit_up"
        assert info["can_buy"] is False
        assert info["can_sell"] is True
        assert "涨停" in info["warning"]

    def test_limit_down_status(self):
        """价格 <= 跌停价：status=limit_down，can_sell=False"""
        prev = 100.0
        limit_down = round(prev * 0.90, 2)  # 90.0
        result = self._build(
            ["000001.SZ"],
            {"000001.SZ": limit_down},
            {"000001.SZ": prev},
        )
        info = result["000001.SZ"]
        assert info["status"] == "limit_down"
        assert info["can_buy"] is True
        assert info["can_sell"] is False
        assert "跌停" in info["warning"]

    def test_missing_prev_close_returns_unknown(self):
        """缺少昨收价：status=unknown，警告含提示"""
        result = self._build(
            ["688001.SH"],
            {"688001.SH": 50.0},
            {},  # 无昨收价
        )
        info = result["688001.SH"]
        assert info["status"] == "unknown"
        assert "昨收价" in info["warning"]

    def test_multiple_tickers(self):
        """多 ticker 时各自独立计算"""
        result = self._build(
            ["600519.SH", "601398.SH"],
            {"600519.SH": 1760.0, "601398.SH": 4.86},  # 茅台涨停，工行跌停
            {"600519.SH": 1600.0, "601398.SH": 5.40},
        )
        assert result["600519.SH"]["status"] == "limit_up"
        assert result["601398.SH"]["status"] == "limit_down"


# ──────────────────────────────────────────────────────────────────
# 2. PMAgent._make_decision() 涨跌停拦截
# ──────────────────────────────────────────────────────────────────

def _make_pm(cash=1_000_000.0):
    """创建最小化 PMAgent 实例（不需要真实 LLM）"""
    from backend.agents.portfolio_manager import PMAgent

    pm = PMAgent.__new__(PMAgent)
    pm.portfolio = {"cash": cash, "positions": {}, "margin_used": 0.0, "margin_requirement": 0.5}
    pm._decisions = {}
    pm._pending_tickers = ["600519.SH", "601398.SH"]
    pm._current_prices = {"600519.SH": 1760.0, "601398.SH": 5.40}
    pm._circuit_breakers = {}
    pm._unlocked_quantities = {}
    return pm


def _get_text(result) -> str:
    """从 ToolResponse 中提取文本（兼容 TextBlock 对象和 dict 两种形式）"""
    item = result.content[0]
    if isinstance(item, dict):
        return item.get("text", "")
    return getattr(item, "text", str(item))


class TestMakeDecisionCircuitBreaker:
    """验证 _make_decision 涨跌停拦截逻辑"""

    def test_long_blocked_on_limit_up(self):
        """涨停股票下 long 应被拦截，返回警告"""
        pm = _make_pm()
        pm._circuit_breakers = {
            "600519.SH": {
                "status": "limit_up",
                "can_buy": False,
                "can_sell": True,
                "current_price": 1760.0,
                "limit_up_price": 1760.0,
                "limit_down_price": 1440.0,
                "warning": "⚠️ 涨停：买单可能无法成交，不建议追涨",
            }
        }
        result = pm._make_decision(
            ticker="600519.SH",
            action="long",
            quantity=100,
            confidence=80,
            reasoning="看多",
        )
        text = _get_text(result)
        assert "涨停" in text
        assert "hold" in text or "改为" in text
        # 决策不应被记录
        assert "600519.SH" not in pm._decisions

    def test_short_blocked_on_limit_down(self):
        """跌停股票下 short 应被拦截，返回警告"""
        pm = _make_pm()
        pm.portfolio["positions"] = {"601398.SH": {"quantity": 500}}
        pm._circuit_breakers = {
            "601398.SH": {
                "status": "limit_down",
                "can_buy": True,
                "can_sell": False,
                "current_price": 4.86,
                "limit_up_price": 5.94,
                "limit_down_price": 4.86,
                "warning": "⚠️ 跌停：卖单可能无法成交，当日退出受阻",
            }
        }
        result = pm._make_decision(
            ticker="601398.SH",
            action="short",
            quantity=200,
            confidence=70,
            reasoning="看空",
        )
        text = _get_text(result)
        assert "跌停" in text
        assert "601398.SH" not in pm._decisions

    def test_normal_stock_long_passes(self):
        """正常股票 long 不应被拦截"""
        pm = _make_pm()
        pm._circuit_breakers = {
            "600519.SH": {
                "status": "normal",
                "can_buy": True,
                "can_sell": True,
                "current_price": 1700.0,
                "limit_up_price": 1760.0,
                "limit_down_price": 1440.0,
                "warning": "",
            }
        }
        result = pm._make_decision(
            ticker="600519.SH",
            action="long",
            quantity=100,
            confidence=75,
            reasoning="基本面好",
        )
        assert "600519.SH" in pm._decisions
        assert pm._decisions["600519.SH"]["action"] == "long"
        assert "✓ Recorded" in _get_text(result)


# ──────────────────────────────────────────────────────────────────
# 3. PMAgent._make_decision() T+1 截断
# ──────────────────────────────────────────────────────────────────

class TestMakeDecisionT1Truncation:
    """验证 T+1 卖出量截断逻辑"""

    def test_t1_truncates_sell_quantity(self):
        """想卖 300 股但 T+1 只允许卖 100 股 → 截断到 100"""
        pm = _make_pm()
        pm._pending_tickers = ["601398.SH"]
        pm._current_prices = {"601398.SH": 5.40}
        pm._circuit_breakers = {}
        pm._unlocked_quantities = {"601398.SH": 100}
        pm.portfolio["positions"] = {"601398.SH": {"quantity": 300}}

        result = pm._make_decision(
            ticker="601398.SH",
            action="short",
            quantity=300,
            confidence=65,
            reasoning="减仓",
        )
        text = _get_text(result)
        assert "T+1截断" in text
        decision = pm._decisions.get("601398.SH", {})
        assert decision["quantity"] == 100

    def test_all_locked_returns_hold(self):
        """全部今日买入（unlocked=0）→ 自动改为 hold"""
        pm = _make_pm()
        pm._pending_tickers = ["601398.SH"]
        pm._current_prices = {"601398.SH": 5.40}
        pm._circuit_breakers = {}
        pm._unlocked_quantities = {"601398.SH": 0}
        pm.portfolio["positions"] = {"601398.SH": {"quantity": 200}}

        result = pm._make_decision(
            ticker="601398.SH",
            action="short",
            quantity=200,
            confidence=65,
            reasoning="减仓",
        )
        text = _get_text(result)
        assert "T+1" in text or "锁仓" in text
        # 决策不应被记录（全部锁仓）
        assert "601398.SH" not in pm._decisions

    def test_no_unlocked_info_allows_sell(self):
        """未提供 unlocked_quantities 时不做 T+1 限制（兼容旧逻辑）"""
        pm = _make_pm()
        pm._pending_tickers = ["601398.SH"]
        pm._current_prices = {"601398.SH": 5.40}
        pm._circuit_breakers = {}
        pm._unlocked_quantities = {}  # 空字典 = 未提供
        pm.portfolio["positions"] = {"601398.SH": {"quantity": 200}}

        result = pm._make_decision(
            ticker="601398.SH",
            action="short",
            quantity=200,
            confidence=65,
            reasoning="减仓",
        )
        assert "601398.SH" in pm._decisions
        assert pm._decisions["601398.SH"]["quantity"] == 200


# ──────────────────────────────────────────────────────────────────
# 4. next_hint 中出现下一个 ticker 的涨跌停信息
# ──────────────────────────────────────────────────────────────────

class TestNextHintIncludesCircuitInfo:
    """决策成功后的 next_hint 应包含下一个 ticker 的涨跌停信息"""

    def test_next_hint_shows_limit_down_warning(self):
        """决策 600519 后，next_hint 应显示 601398 跌停警告"""
        pm = _make_pm()
        pm._pending_tickers = ["600519.SH", "601398.SH"]
        pm._current_prices = {"600519.SH": 1700.0, "601398.SH": 4.86}
        pm._circuit_breakers = {
            "600519.SH": {"status": "normal", "can_buy": True, "can_sell": True, "warning": ""},
            "601398.SH": {
                "status": "limit_down",
                "can_buy": True,
                "can_sell": False,
                "current_price": 4.86,
                "limit_up_price": 5.94,
                "limit_down_price": 4.86,
                "warning": "⚠️ 跌停：卖单可能无法成交，当日退出受阻",
            },
        }
        pm._unlocked_quantities = {"600519.SH": 0, "601398.SH": 200}

        result = pm._make_decision(
            ticker="600519.SH",
            action="hold",
            quantity=0,
            confidence=60,
            reasoning="中性",
        )
        text = _get_text(result)
        assert "跌停" in text, f"next_hint 应包含跌停警告，实际: {text}"
        assert "601398.SH" in text

    def test_next_hint_shows_t1_unlocked(self):
        """next_hint 应显示下一个 ticker 的 T+1 可卖量"""
        pm = _make_pm()
        pm._pending_tickers = ["600519.SH", "601398.SH"]
        pm._current_prices = {"600519.SH": 1700.0, "601398.SH": 5.40}
        pm._circuit_breakers = {}
        pm._unlocked_quantities = {"600519.SH": 0, "601398.SH": 100}

        result = pm._make_decision(
            ticker="600519.SH",
            action="hold",
            quantity=0,
            confidence=60,
            reasoning="中性",
        )
        text = _get_text(result)
        assert "T+1 可卖量" in text
        assert "100" in text
