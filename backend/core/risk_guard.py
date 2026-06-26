# -*- coding: utf-8 -*-
"""
RiskGuard - 硬约束规则引擎

在 PM 生成交易指令之后、实际执行之前运行。
与 risk_agent（LLM 软约束建议）不同，这里的规则是不可商量的强制边界。

三条规则：
1. 仓位上限（position_limit）     : 单票市值不超过总资产的 X%
2. 单日亏损熔断（drawdown_circuit）: 当天亏损超过阈值，禁止新开仓
3. 行业集中度（sector_concentration）: 单行业市值不超过总资产的 X%
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# A股行业分类（根据当前实验股票池硬编码，生产环境应从数据源获取）
SECTOR_MAP: Dict[str, str] = {
    "600519.SH": "消费",
    "000858.SZ": "消费",
    "000333.SZ": "家电制造",
    "300750.SZ": "新能源",
    "600030.SH": "金融",
    "601398.SH": "金融",
    "601318.SH": "金融",
    "600276.SH": "医药",
}


class RiskGuard:
    """
    硬约束规则引擎。

    用法：
        guard = RiskGuard(
            position_limit=0.30,       # 单票仓位上限 30%
            drawdown_limit=0.05,       # 单日亏损熔断阈值 5%
            sector_limit=0.60,         # 单行业仓位上限 60%
        )
        decisions = guard.apply(decisions, portfolio, open_prices, open_nav)
    """

    def __init__(
        self,
        position_limit: float = 0.30,
        drawdown_limit: float = 0.05,
        sector_limit: float = 0.60,
    ):
        self.position_limit = position_limit
        self.drawdown_limit = drawdown_limit
        self.sector_limit = sector_limit

    # ── 对外入口 ────────────────────────────────────────────────────────────

    def apply(
        self,
        decisions: Dict[str, Any],
        portfolio: Dict[str, Any],
        open_prices: Dict[str, float],
        open_nav: float,
    ) -> Dict[str, Any]:
        """
        依次应用三条硬约束，返回修改后的 decisions。

        Args:
            decisions   : PM 输出的交易指令，格式同 pipeline 中 decisions dict
            portfolio   : 当前持仓状态（来自 pm.get_portfolio_state()）
            open_prices : 今日开盘价
            open_nav    : 今日开盘时总资产（现金 + 持仓按开盘价估值）
                          作为单日亏损熔断的基准线
        """
        if open_nav <= 0:
            return decisions

        triggered = []

        # ── 规则1：单日亏损熔断 ─────────────────────────────────────────────
        # 当前 NAV 由 pipeline 在每日结算时计算，这里用开盘 NAV 作基准。
        # 熔断逻辑：若当前资产已经比开盘低超过阈值，禁止所有新开仓。
        cash = self._get_cash(portfolio)
        current_nav = cash + self._positions_value(portfolio, open_prices)
        drawdown = (open_nav - current_nav) / open_nav

        if drawdown >= self.drawdown_limit:
            triggered.append(
                f"🔴 熔断触发：当日亏损 {drawdown:.1%} ≥ 阈值 {self.drawdown_limit:.1%}，禁止新开仓"
            )
            decisions = self._block_new_buys(decisions)

        # ── 规则2：单票仓位上限 ─────────────────────────────────────────────
        decisions, msgs = self._apply_position_limit(
            decisions, portfolio, open_prices, open_nav
        )
        triggered.extend(msgs)

        # ── 规则3：行业集中度上限 ───────────────────────────────────────────
        decisions, msgs = self._apply_sector_limit(
            decisions, portfolio, open_prices, open_nav
        )
        triggered.extend(msgs)

        for msg in triggered:
            logger.warning("[RiskGuard] %s", msg)
            print(f"   ⚠️  RiskGuard: {msg}")

        return decisions

    # ── 规则实现 ─────────────────────────────────────────────────────────────

    def _apply_position_limit(
        self,
        decisions: Dict[str, Any],
        portfolio: Dict[str, Any],
        open_prices: Dict[str, float],
        open_nav: float,
    ):
        msgs = []
        for ticker, decision in decisions.items():
            action = decision.get("action", "hold")
            qty = decision.get("quantity", 0)
            if action != "long" or qty <= 0:
                continue

            price = open_prices.get(ticker, 0)
            if price <= 0:
                continue

            # 买入后该票市值
            existing_qty = self._get_long_qty(portfolio, ticker)
            total_qty = existing_qty + qty
            projected_value = total_qty * price
            ratio = projected_value / open_nav

            if ratio > self.position_limit:
                # 限制到上限允许的股数（向下取整到整手 100 股）
                max_value = open_nav * self.position_limit
                max_qty = int((max_value - existing_qty * price) / price)
                max_qty = max(0, (max_qty // 100) * 100)  # A股整手

                msgs.append(
                    f"仓位上限：{ticker} 买入后占比 {ratio:.1%} > {self.position_limit:.1%}"
                    f"，数量从 {qty} 裁剪至 {max_qty}"
                )
                decision["quantity"] = max_qty
                if max_qty == 0:
                    decision["action"] = "hold"

        return decisions, msgs

    def _apply_sector_limit(
        self,
        decisions: Dict[str, Any],
        portfolio: Dict[str, Any],
        open_prices: Dict[str, float],
        open_nav: float,
    ):
        msgs = []

        # 计算买入后各行业的预估市值
        sector_values: Dict[str, float] = {}

        # 现有持仓
        for ticker, pos in portfolio.get("positions", {}).items():
            sector = SECTOR_MAP.get(ticker, "其他")
            price = open_prices.get(ticker, 0)
            qty = self._get_long_qty(portfolio, ticker)
            sector_values[sector] = sector_values.get(sector, 0) + qty * price

        # 加上本次买入
        for ticker, decision in decisions.items():
            if decision.get("action") != "long":
                continue
            qty = decision.get("quantity", 0)
            price = open_prices.get(ticker, 0)
            sector = SECTOR_MAP.get(ticker, "其他")
            sector_values[sector] = sector_values.get(sector, 0) + qty * price

        # 检查是否超限
        for ticker, decision in decisions.items():
            if decision.get("action") != "long" or decision.get("quantity", 0) <= 0:
                continue
            sector = SECTOR_MAP.get(ticker, "其他")
            ratio = sector_values.get(sector, 0) / open_nav
            if ratio > self.sector_limit:
                msgs.append(
                    f"行业集中度：{sector} 行业买入后占比 {ratio:.1%} > {self.sector_limit:.1%}"
                    f"，{ticker} 买入指令取消"
                )
                decision["quantity"] = 0
                decision["action"] = "hold"

        return decisions, msgs

    def _block_new_buys(self, decisions: Dict[str, Any]) -> Dict[str, Any]:
        """熔断时禁止所有新开多仓"""
        for ticker, decision in decisions.items():
            if decision.get("action") == "long":
                decision["action"] = "hold"
                decision["quantity"] = 0
        return decisions

    # ── 工具方法 ─────────────────────────────────────────────────────────────

    def _get_cash(self, portfolio: Dict[str, Any]) -> float:
        return float(portfolio.get("cash", 0))

    def _get_long_qty(self, portfolio: Dict[str, Any], ticker: str) -> int:
        pos = portfolio.get("positions", {}).get(ticker, {})
        if isinstance(pos, dict):
            return int(pos.get("long", pos.get("quantity", 0)))
        return int(pos)

    def _positions_value(
        self, portfolio: Dict[str, Any], prices: Dict[str, float]
    ) -> float:
        total = 0.0
        for ticker, pos in portfolio.get("positions", {}).items():
            qty = self._get_long_qty(portfolio, ticker)
            total += qty * prices.get(ticker, 0)
        return total
