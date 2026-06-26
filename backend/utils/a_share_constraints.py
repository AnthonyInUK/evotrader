# -*- coding: utf-8 -*-
"""
A股交易约束引擎

将美股规则的 PortfolioTradeExecutor 包装成符合A股市场规则的版本。

核心约束：
  1. T+1 制度：当日买入的股票，次日才能卖出（当日锁仓）
  2. 涨跌停板：普通股 ±10%，ST股 ±5%，触板后买/卖受限
  3. 印花税：卖出时收取 0.1%（买入不收）
  4. 佣金：买卖双向，一般 0.03%，最低 5 元
  5. 做空禁止：A股散户不能做空（融券门槛极高，回测中禁用）
  6. 手数限制：买入必须是 100 股（1手）的整数倍，卖出可以是零股

⚠️ 开发问题记录：
  Q: 为什么不直接修改 PortfolioTradeExecutor？
  A: 因为原来的 executor 还要用于美股回测（ret_data/ 里有 AAPL 等），
     我们用继承而不是直接改，保持向后兼容。
"""
import logging
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .trade_executor import PortfolioTradeExecutor

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────────────────────────

LOT_SIZE = 100          # 1手 = 100股
STAMP_DUTY_RATE = 0.001 # 印花税：卖出 0.1%
COMMISSION_RATE = 0.0003  # 佣金：0.03%（双向）
MIN_COMMISSION = 5.0    # 最低佣金 5 元
TRANSFER_FEE_RATE = 0.00002  # 过户费（沪市）：0.002%

NORMAL_LIMIT_RATE = 0.10   # 普通股涨跌幅 ±10%
ST_LIMIT_RATE = 0.05       # ST股涨跌幅 ±5%


# ──────────────────────────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────────────────────────

def is_st_stock(symbol: str) -> bool:
    """
    判断是否是 ST/ST* 股票。

    实际项目中应该查数据库或接口。
    这里用 ticker 前缀做简单判断（占位）：
      - 如果 symbol 包含 "ST" 字样（如传入了股票名）→ 是 ST
      - 否则默认为普通股

    TODO: Day 8 接入 finance-mcp 的 crawl_ths_field 工具获取实时ST状态
    """
    return "ST" in symbol.upper()


def get_limit_rate(symbol: str) -> float:
    """获取该股票的涨跌停幅度"""
    return ST_LIMIT_RATE if is_st_stock(symbol) else NORMAL_LIMIT_RATE


def calc_limit_up_price(symbol: str, prev_close: float) -> float:
    """计算涨停价（向上取整到分）"""
    rate = get_limit_rate(symbol)
    raw = prev_close * (1 + rate)
    return round(raw, 2)


def calc_limit_down_price(symbol: str, prev_close: float) -> float:
    """计算跌停价（向下取整到分）"""
    rate = get_limit_rate(symbol)
    raw = prev_close * (1 - rate)
    return round(raw, 2)


def build_circuit_status(
    tickers: List[str],
    prices: Dict[str, float],
    prev_closes: Dict[str, float],
) -> Dict[str, Dict]:
    """
    为每个 ticker 生成涨跌停状态摘要，供 PM 决策时参考。

    返回格式示例：
    {
        "600519.SH": {
            "status": "normal",          # "normal" | "limit_up" | "limit_down" | "unknown"
            "current_price": 1700.0,
            "limit_up_price": 1870.5,
            "limit_down_price": 1529.5,
            "can_buy": True,
            "can_sell": True,
            "warning": "",
        },
        "601398.SH": {
            "status": "limit_down",
            ...
            "can_sell": False,
            "warning": "⚠️ 跌停：卖单可能无法成交",
        },
    }

    Args:
        tickers:     需要评估的股票代码列表
        prices:      当日价格 {ticker: price}（开盘价或最新价）
        prev_closes: 昨日收盘价 {ticker: prev_close}（用于计算涨跌停价格）
    """
    result: Dict[str, Dict] = {}
    for ticker in tickers:
        price = prices.get(ticker, 0.0)
        prev = prev_closes.get(ticker, 0.0)

        if not price or not prev:
            result[ticker] = {
                "status": "unknown",
                "current_price": price,
                "limit_up_price": 0.0,
                "limit_down_price": 0.0,
                "can_buy": True,
                "can_sell": True,
                "warning": "⚠️ 缺少昨收价，无法计算涨跌停价格",
            }
            continue

        limit_up = calc_limit_up_price(ticker, prev)
        limit_down = calc_limit_down_price(ticker, prev)

        # 判断状态（留 0.01 浮点容差）
        if price >= limit_up - 0.01:
            status = "limit_up"
            can_buy = False   # 涨停板：挂买单大概率无法成交
            can_sell = True   # 涨停板：可以卖出（若有人愿意接）
            warning = "⚠️ 涨停：买单可能无法成交，不建议追涨"
        elif price <= limit_down + 0.01:
            status = "limit_down"
            can_buy = True    # 跌停板：可以挂买单（抄底）
            can_sell = False  # 跌停板：卖单极难成交，警惕锁仓
            warning = "⚠️ 跌停：卖单可能无法成交，当日退出受阻"
        else:
            status = "normal"
            can_buy = True
            can_sell = True
            warning = ""

        result[ticker] = {
            "status": status,
            "current_price": round(price, 2),
            "limit_up_price": limit_up,
            "limit_down_price": limit_down,
            "can_buy": can_buy,
            "can_sell": can_sell,
            "warning": warning,
        }
    return result


def round_to_lot(quantity: int) -> int:
    """
    将买入数量向下取整到 100 股的整数倍。

    A股规则：买入必须是 100 股（1手）的整数倍。
    卖出时可以卖零股（把剩余不足100股全卖掉）。

    例：想买 150 股 → 只能买 100 股（向下取整）
    """
    return (quantity // LOT_SIZE) * LOT_SIZE


def calc_transaction_cost(
    action: str,
    quantity: int,
    price: float,
    symbol: str = "",
) -> Dict[str, float]:
    """
    计算A股单笔交易的全部费用。

    买入费用：
      - 佣金 = max(成交额 × 0.03%, 5元)
      - 过户费（仅沪市）= 成交额 × 0.002%

    卖出费用（比买入多一项）：
      - 佣金 = max(成交额 × 0.03%, 5元)
      - 印花税 = 成交额 × 0.1%  ← 最大头
      - 过户费（仅沪市）= 成交额 × 0.002%

    Args:
        action: "buy" 或 "sell"
        quantity: 股数
        price: 单价
        symbol: ticker（用于判断沪市/深市）

    Returns:
        {
            "commission": float,      # 佣金
            "stamp_duty": float,      # 印花税（仅卖出）
            "transfer_fee": float,    # 过户费（仅沪市）
            "total": float            # 总费用
        }
    """
    trade_value = quantity * price

    # 佣金（买卖双向）
    commission = max(trade_value * COMMISSION_RATE, MIN_COMMISSION)

    # 印花税（仅卖出，这是A股特有的大头成本）
    stamp_duty = trade_value * STAMP_DUTY_RATE if action == "sell" else 0.0

    # 过户费（仅沪市，代码以 6 开头）
    is_shanghai = symbol.startswith("6") or symbol.endswith(".SH")
    transfer_fee = trade_value * TRANSFER_FEE_RATE if is_shanghai else 0.0

    total = commission + stamp_duty + transfer_fee

    return {
        "commission": commission,
        "stamp_duty": stamp_duty,
        "transfer_fee": transfer_fee,
        "total": total,
    }


# ──────────────────────────────────────────────────────────────────
# 数据类：涨跌停状态
# ──────────────────────────────────────────────────────────────────

@dataclass
class LimitCheckResult:
    """涨跌停检查结果"""
    is_limit_up: bool = False    # 是否涨停
    is_limit_down: bool = False  # 是否跌停
    limit_up_price: float = 0.0
    limit_down_price: float = 0.0
    can_buy: bool = True         # 能否买入
    can_sell: bool = True        # 能否卖出
    reason: str = ""


@dataclass
class OrderValidationResult:
    """订单验证结果"""
    approved: bool = True
    adjusted_quantity: int = 0   # 调整后的数量（可能因手数规则被调整）
    rejection_reason: str = ""
    warnings: List[str] = field(default_factory=list)
    transaction_cost: float = 0.0


# ──────────────────────────────────────────────────────────────────
# 核心约束类（纯逻辑，无状态）
# ──────────────────────────────────────────────────────────────────

class AShareConstraints:
    """
    A股交易规则校验器（无状态，纯函数集合）

    设计原则：这个类只做"判断"，不做"执行"。
    执行层（ASharePortfolioTradeExecutor）负责调用这里的检查，
    并根据结果决定是否真正执行交易。
    """

    @staticmethod
    def check_limit(
        symbol: str,
        current_price: float,
        prev_close: float,
    ) -> LimitCheckResult:
        """
        检查是否触发涨跌停板。

        ⚠️ 重要设计细节：
          涨停 → 理论上"可以卖"（卖出不受限），但"买不进去"（买单挂不上）
          跌停 → 理论上"可以买"（买单挂得上），但"卖不出去"（卖单无人接）

        但在回测中，我们用收盘价判断，如果收盘价就是涨停价，
        大概率今天买不到（大量人在排队买，成交极难），
        保守处理：涨停拒绝买入，跌停拒绝卖出。

        Args:
            symbol: ticker
            current_price: 当前价格（或收盘价）
            prev_close: 昨日收盘价
        """
        result = LimitCheckResult()
        result.limit_up_price = calc_limit_up_price(symbol, prev_close)
        result.limit_down_price = calc_limit_down_price(symbol, prev_close)

        # 涨停判断：当前价 >= 涨停价（允许 0.01 的浮点误差）
        if current_price >= result.limit_up_price - 0.01:
            result.is_limit_up = True
            result.can_buy = False
            result.reason = (
                f"涨停板（{current_price:.2f} >= 涨停价 {result.limit_up_price:.2f}），"
                f"买入订单无法成交"
            )

        # 跌停判断
        elif current_price <= result.limit_down_price + 0.01:
            result.is_limit_down = True
            result.can_sell = False
            result.reason = (
                f"跌停板（{current_price:.2f} <= 跌停价 {result.limit_down_price:.2f}），"
                f"卖出订单无法成交"
            )

        return result

    @staticmethod
    def validate_buy_order(
        symbol: str,
        quantity: int,
        price: float,
        prev_close: float,
        available_cash: float,
    ) -> OrderValidationResult:
        """
        验证买入订单是否可以执行。

        检查清单（按顺序）：
          1. 价格有效性
          2. 做空限制（A股不允许做空）→ 这里只验证买入
          3. 涨停板拦截
          4. 手数取整（向下到100股整数倍）
          5. 资金充足性（含手续费）
        """
        result = OrderValidationResult(approved=True)

        # ── 1. 价格有效 ──────────────────────────────────────────
        if price <= 0 or prev_close <= 0:
            result.approved = False
            result.rejection_reason = f"无效价格: price={price}, prev_close={prev_close}"
            return result

        # ── 2. 涨停板检查 ────────────────────────────────────────
        limit_check = AShareConstraints.check_limit(symbol, price, prev_close)
        if not limit_check.can_buy:
            result.approved = False
            result.rejection_reason = limit_check.reason
            return result

        # ── 3. 手数取整（100股整数倍）────────────────────────────
        adjusted_qty = round_to_lot(quantity)
        if adjusted_qty <= 0:
            result.approved = False
            result.rejection_reason = (
                f"买入数量 {quantity} 股不足 1 手（100股），无法成交"
            )
            return result

        if adjusted_qty != quantity:
            result.warnings.append(
                f"买入数量由 {quantity} 股调整为 {adjusted_qty} 股（取整到100股整数倍）"
            )
        result.adjusted_quantity = adjusted_qty

        # ── 4. 资金充足性检查（含手续费）────────────────────────
        cost = calc_transaction_cost("buy", adjusted_qty, price, symbol)
        total_needed = adjusted_qty * price + cost["total"]

        if available_cash < total_needed:
            # 尝试减少买入数量直到资金够用
            affordable_value = available_cash - MIN_COMMISSION
            affordable_qty = int(affordable_value / price)
            affordable_qty = round_to_lot(affordable_qty)

            if affordable_qty <= 0:
                result.approved = False
                result.rejection_reason = (
                    f"资金不足：需要 ¥{total_needed:.2f}，"
                    f"可用 ¥{available_cash:.2f}"
                )
                return result
            else:
                result.warnings.append(
                    f"资金不足，买入数量由 {adjusted_qty} 股缩减为 {affordable_qty} 股"
                )
                adjusted_qty = affordable_qty
                cost = calc_transaction_cost("buy", adjusted_qty, price, symbol)

        result.adjusted_quantity = adjusted_qty
        result.transaction_cost = cost["total"]
        return result

    @staticmethod
    def validate_sell_order(
        symbol: str,
        quantity: int,
        price: float,
        prev_close: float,
        available_shares: int,  # T+1 后可卖的股数（不含今日买入）
    ) -> OrderValidationResult:
        """
        验证卖出订单是否可以执行。

        检查清单：
          1. 价格有效性
          2. 跌停板拦截
          3. 持仓充足性（这里传入的是 T+1 过滤后的可卖数量）

        注意：T+1 的过滤在 ASharePortfolioTradeExecutor 里做，
              这里收到的 available_shares 已经是"可卖股数"了。
        """
        result = OrderValidationResult(approved=True)

        # ── 1. 价格有效 ──────────────────────────────────────────
        if price <= 0 or prev_close <= 0:
            result.approved = False
            result.rejection_reason = f"无效价格: {price}"
            return result

        # ── 2. 跌停板检查 ────────────────────────────────────────
        limit_check = AShareConstraints.check_limit(symbol, price, prev_close)
        if not limit_check.can_sell:
            result.approved = False
            result.rejection_reason = limit_check.reason
            return result

        # ── 3. 持仓充足性 ────────────────────────────────────────
        sell_qty = min(quantity, available_shares)
        if sell_qty <= 0:
            result.approved = False
            result.rejection_reason = (
                f"无可卖股份（持仓 {available_shares} 股均为今日买入，T+1 锁仓）"
                if available_shares <= 0
                else f"无持仓可卖"
            )
            return result

        if sell_qty != quantity:
            result.warnings.append(
                f"卖出数量由 {quantity} 调整为 {sell_qty}（受 T+1 或持仓限制）"
            )

        result.adjusted_quantity = sell_qty
        cost = calc_transaction_cost("sell", sell_qty, price, symbol)
        result.transaction_cost = cost["total"]
        return result


# ──────────────────────────────────────────────────────────────────
# A股版本的交易执行器
# ──────────────────────────────────────────────────────────────────

class ASharePortfolioTradeExecutor(PortfolioTradeExecutor):
    """
    A股版 Portfolio 执行器

    继承美股版 PortfolioTradeExecutor，在执行前插入A股约束检查。

    新增状态：
      - _locked_shares: {date: {symbol: qty}} — T+1 锁仓记录
      - _prev_closes: {symbol: float} — 昨日收盘价（用于涨跌停计算）
      - _constraint_log: 约束拦截日志（用于复盘分析）

    ⚠️ 设计决策记录：
      为什么用继承而不是组合（装饰器模式）？
      因为 gateway.py 里对 executor 的调用很深（execute_trades → _execute_single_trade → ...），
      继承可以只 override 关键方法，改动最小。
      如果用装饰器，需要在 gateway 层加很多判断。
    """

    def __init__(self, initial_portfolio: Optional[Dict[str, Any]] = None):
        super().__init__(initial_portfolio)

        # T+1 锁仓：{买入日期: {symbol: 买入股数}}
        # 例：{"2024-01-02": {"600519.SH": 100}}
        # 在 set_date 时清理过期锁仓（超过1天的自动解锁）
        self._locked_shares: Dict[str, Dict[str, int]] = defaultdict(dict)

        # 昨日收盘价（用于计算涨跌停价格）
        # 在回测主循环里，每天 set_prev_closes() 更新
        self._prev_closes: Dict[str, float] = {}

        # 约束拦截日志（面试 / 复盘用）
        self._constraint_log: List[Dict[str, Any]] = []

        # 今日日期（用于 T+1 判断）
        self._current_date: Optional[str] = None

    def set_date(self, date_str: str) -> None:
        """
        推进到下一个交易日（回测主循环每天调用）。

        同时释放 T+1 锁仓：
          如果今天是 2024-01-03，则 2024-01-02 买入的股票今天可以卖了。
        """
        self._current_date = date_str
        self._release_expired_locks(date_str)

    def set_prev_closes(self, prev_closes: Dict[str, float]) -> None:
        """
        更新昨日收盘价（每天 emit_open_prices 前调用）。
        涨跌停计算依赖这个数据。
        """
        self._prev_closes.update(prev_closes)

    def get_available_shares(self, symbol: str) -> int:
        """
        获取 T+1 规则下可卖出的股数。

        = 总持仓 - 今日买入锁仓

        例：总持仓 300 股，今天买了 100 股 → 可卖 200 股
        """
        total_long = self.portfolio["positions"].get(symbol, {}).get("long", 0)
        locked_today = self._locked_shares.get(self._current_date, {}).get(symbol, 0)
        return max(0, total_long - locked_today)

    def _lock_shares(self, symbol: str, quantity: int) -> None:
        """买入后立即锁仓（T+1）"""
        if self._current_date:
            self._locked_shares[self._current_date][symbol] = (
                self._locked_shares[self._current_date].get(symbol, 0) + quantity
            )
            logger.debug(f"T+1 锁仓：{symbol} 锁定 {quantity} 股（{self._current_date}）")

    def _release_expired_locks(self, current_date: str) -> None:
        """
        释放超过 1 天的锁仓。

        T+1：买入日次日即可卖出。
        例：2024-01-02 买入 → 2024-01-03 开始可卖。
        """
        current_dt = datetime.strptime(current_date, "%Y-%m-%d").date()
        expired_dates = [
            d for d in list(self._locked_shares.keys())
            if datetime.strptime(d, "%Y-%m-%d").date() < current_dt
        ]
        for d in expired_dates:
            released = self._locked_shares.pop(d)
            if released:
                logger.debug(f"T+1 解锁：{d} 的锁仓已释放：{released}")

    def _log_constraint(
        self,
        date: str,
        symbol: str,
        action: str,
        original_qty: int,
        result_qty: int,
        reason: str,
        blocked: bool,
    ) -> None:
        """记录约束拦截事件（用于后续分析：被拦了多少次？损失了多少收益？）"""
        self._constraint_log.append({
            "date": date,
            "symbol": symbol,
            "action": action,
            "original_qty": original_qty,
            "result_qty": result_qty,
            "reason": reason,
            "blocked": blocked,
        })

    # ──────────────────────────────────────────────────────────────
    # Override 核心交易方法（加入A股约束）
    # ──────────────────────────────────────────────────────────────

    def _buy_long_position(
        self,
        ticker: str,
        quantity: int,
        price: float,
        date_str: str,
    ) -> Dict[str, Any]:
        """
        买入（A股版）

        在父类逻辑前插入：
          1. 涨停板检查
          2. 手数取整
          3. 含手续费的资金检查
        买入成功后：T+1 锁仓
        """
        prev_close = self._prev_closes.get(ticker, price)
        available_cash = self.portfolio["cash"]

        # A股约束验证
        validation = AShareConstraints.validate_buy_order(
            symbol=ticker,
            quantity=quantity,
            price=price,
            prev_close=prev_close,
            available_cash=available_cash,
        )

        # 打印警告（数量被调整）
        for warning in validation.warnings:
            logger.warning(f"⚠️  {ticker}: {warning}")

        if not validation.approved:
            self._log_constraint(
                date=date_str, symbol=ticker, action="buy",
                original_qty=quantity, result_qty=0,
                reason=validation.rejection_reason, blocked=True,
            )
            logger.warning(f"🚫 {ticker} 买入被拦截: {validation.rejection_reason}")
            return {
                "status": "failed",
                "ticker": ticker,
                "action": "buy",
                "quantity": quantity,
                "price": price,
                "reason": validation.rejection_reason,
            }

        adjusted_qty = validation.adjusted_quantity
        trade_value = adjusted_qty * price
        cost = calc_transaction_cost("buy", adjusted_qty, price, ticker)
        total_cost = trade_value + cost["total"]

        # 资金充足性（含手续费）
        if self.portfolio["cash"] < total_cost:
            reason = (
                f"资金不足（需 ¥{total_cost:.2f}，"
                f"含手续费 ¥{cost['total']:.2f}，"
                f"可用 ¥{self.portfolio['cash']:.2f}）"
            )
            self._log_constraint(date_str, ticker, "buy", quantity, 0, reason, True)
            return {
                "status": "failed",
                "ticker": ticker,
                "action": "buy",
                "quantity": adjusted_qty,
                "price": price,
                "reason": reason,
            }

        # ── 执行买入（更新持仓 + 扣款 + T+1 锁仓）─────────────
        if ticker not in self.portfolio["positions"]:
            self.portfolio["positions"][ticker] = {
                "long": 0, "short": 0,
                "long_cost_basis": 0.0, "short_cost_basis": 0.0,
            }

        position = self.portfolio["positions"][ticker]
        old_long = position["long"]
        new_long = old_long + adjusted_qty

        # 更新成本均价
        if new_long > 0:
            position["long_cost_basis"] = (
                (old_long * position["long_cost_basis"]) + (adjusted_qty * price)
            ) / new_long
        position["long"] = new_long

        # 扣除买入金额 + 手续费
        self.portfolio["cash"] -= total_cost

        # T+1 锁仓
        self._lock_shares(ticker, adjusted_qty)

        if adjusted_qty != quantity:
            self._log_constraint(
                date_str, ticker, "buy", quantity, adjusted_qty,
                f"手数/资金调整", blocked=False,
            )

        logger.info(
            f"✅ {ticker} 买入 {adjusted_qty} 股 @ ¥{price:.2f} "
            f"（手续费 ¥{cost['total']:.2f}，T+1 锁仓至明日）"
        )
        return {"status": "success"}

    def _sell_long_position(
        self,
        ticker: str,
        quantity: int,
        price: float,
        date_str: str,
    ) -> Dict[str, Any]:
        """
        卖出（A股版）

        在父类逻辑前插入：
          1. 跌停板检查
          2. T+1 可卖数量检查
          3. 印花税计算（卖出多扣一笔）
        """
        prev_close = self._prev_closes.get(ticker, price)
        available_shares = self.get_available_shares(ticker)

        # A股约束验证
        validation = AShareConstraints.validate_sell_order(
            symbol=ticker,
            quantity=quantity,
            price=price,
            prev_close=prev_close,
            available_shares=available_shares,
        )

        for warning in validation.warnings:
            logger.warning(f"⚠️  {ticker}: {warning}")

        if not validation.approved:
            self._log_constraint(
                date_str, ticker, "sell", quantity, 0,
                validation.rejection_reason, True,
            )
            logger.warning(f"🚫 {ticker} 卖出被拦截: {validation.rejection_reason}")
            return {
                "status": "failed",
                "ticker": ticker,
                "action": "sell",
                "quantity": quantity,
                "price": price,
                "reason": validation.rejection_reason,
            }

        adjusted_qty = validation.adjusted_quantity
        cost = calc_transaction_cost("sell", adjusted_qty, price, ticker)
        trade_proceeds = adjusted_qty * price - cost["total"]  # 卖出到手金额

        # ── 执行卖出（减持仓 + 到账）──────────────────────────
        position = self.portfolio["positions"][ticker]
        position["long"] -= adjusted_qty
        if position["long"] == 0:
            position["long_cost_basis"] = 0.0

        self.portfolio["cash"] += trade_proceeds

        logger.info(
            f"✅ {ticker} 卖出 {adjusted_qty} 股 @ ¥{price:.2f} "
            f"（印花税 ¥{cost['stamp_duty']:.2f}，"
            f"手续费 ¥{cost['commission']:.2f}，"
            f"实收 ¥{trade_proceeds:.2f}）"
        )
        return {"status": "success"}

    def _open_short_position(
        self,
        ticker: str,
        quantity: int,
        price: float,
        date_str: str,
    ) -> Dict[str, Any]:
        """
        做空 → A股直接拒绝。

        ⚠️ A股规则：散户不能做空（没有融券资格）。
        即使有融券，也需要单独申请，门槛极高，回测中统一禁用。
        """
        reason = "A股不支持做空（T+1 现货市场，无融券资格）"
        self._log_constraint(date_str, ticker, "short", quantity, 0, reason, True)
        logger.warning(f"🚫 {ticker} 做空被拒绝: {reason}")
        return {
            "status": "failed",
            "ticker": ticker,
            "action": "short",
            "quantity": quantity,
            "price": price,
            "reason": reason,
        }

    def get_constraint_summary(self) -> Dict[str, Any]:
        """
        获取约束拦截统计（用于回测报告和面试讲解）

        输出示例：
            总拦截: 12 次
            涨停拦截（错过了多少买入）: 5 次
            跌停拦截（卖不出去的亏损）: 3 次
            T+1 锁仓拦截: 4 次
        """
        total = len(self._constraint_log)
        blocked = [e for e in self._constraint_log if e["blocked"]]

        return {
            "total_constraint_events": total,
            "blocked_orders": len(blocked),
            "constraint_log": self._constraint_log,
            "blocked_by_type": {
                "limit_up": sum(1 for e in blocked if "涨停" in e["reason"]),
                "limit_down": sum(1 for e in blocked if "跌停" in e["reason"]),
                "t_plus_1": sum(1 for e in blocked if "T+1" in e["reason"]),
                "short_ban": sum(1 for e in blocked if "做空" in e["reason"]),
                "insufficient_cash": sum(1 for e in blocked if "资金" in e["reason"]),
            },
        }
