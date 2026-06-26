# -*- coding: utf-8 -*-
"""
实验 B：约束触发记录

验证问题：如果不加 A 股执行约束，回测结果会在哪些地方悄悄失真？

实验设计：
  - 构造一段 10 个交易日的价格序列，主动植入 3 类触发场景：
      Day 3:  涨停（+10%）→ PM 尝试追买
      Day 6:  T+1 当日买当日卖
      Day 9:  跌停（-10%）→ PM 尝试卖出
  - "无约束执行器"：接受所有 PM 指令，不做任何约束检查
  - "A股约束执行器"：T+1 / 涨跌停 / 手数 / 印花税全部生效
  - 对比每笔交易的执行结果和最终 P&L 差异

运行方式：
    cd evotraders/backend
    python experiments/experiment_constraint_triggers.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.utils.a_share_constraints import ASharePortfolioTradeExecutor
from backend.utils.trade_executor import PortfolioTradeExecutor

INITIAL_CASH = 1_000_000.0
TICKER = "000001.SZ"

# ──────────────────────────────────────────────────────────────────
# 价格序列设计（3 个独立场景，依次触发）
#
# 【场景1：涨停追买】
#   Day1  2024-03-01  prev=100  open=102    正常建仓 200 股
#   Day2  2024-03-04  prev=103  open=115.5  涨停(+10%)  PM 追买 → 应被拦截
#   Day3  2024-03-05  prev=115.5 open=117   涨停次日，正常
#
# 【场景2：T+1 当日买当日卖】
#   Day4  2024-03-06  prev=117  open=115    先清仓（卖出 200 股）
#   Day5  2024-03-07  prev=115  open=113    全新买入 100 股（从 0 仓开始）
#   Day5  同日         same      same        当天立刻卖出 100 股 → 应被拦截
#   Day6  2024-03-08  prev=113  open=114    次日卖出 → 正常成交
#
# 【场景3：跌停止损】
#   Day7  2024-03-11  prev=114  open=120    建仓 200 股
#   Day8  2024-03-12  prev=120  open=108    跌停(-10%)  PM 止损 → 应被拦截
#   Day9  2024-03-13  prev=108  open=110    跌停次日，正常卖出
# ──────────────────────────────────────────────────────────────────

@dataclass
class DayData:
    date: str
    prev_close: float
    open_price: float
    close_price: float
    scenario: str = ""  # 场景说明


PRICE_SERIES: List[DayData] = [
    # 场景1：涨停
    DayData("2024-03-01", 100.0, 102.0, 103.0, "正常开盘"),
    DayData("2024-03-04", 103.0, 113.3, 113.3, "【涨停】+10%"),  # 103 * 1.10 = 113.3
    DayData("2024-03-05", 113.3, 115.0, 114.0, "涨停次日，正常"),
    # 场景2：T+1
    DayData("2024-03-06", 114.0, 112.0, 111.0, "清仓日"),
    DayData("2024-03-07", 111.0, 110.0, 109.0, "【T+1】从零买入，当日尝试卖出"),
    DayData("2024-03-08", 109.0, 110.0, 111.0, "T+1 次日，正常卖出"),
    # 场景3：跌停
    DayData("2024-03-11", 111.0, 113.0, 114.0, "建仓日"),
    DayData("2024-03-12", 114.0, 102.6, 102.6, "【跌停】-10%"),  # 114 * 0.90 = 102.6
    DayData("2024-03-13", 102.6, 104.0, 105.0, "跌停次日，正常卖出"),
]


# ──────────────────────────────────────────────────────────────────
# PM 策略：简单规则，主动触发三类约束
# ──────────────────────────────────────────────────────────────────

@dataclass
class PMInstruction:
    date: str
    action: str       # "long" | "short"
    quantity: int
    price: float
    intent: str       # 人类可读的意图说明


def build_pm_instructions() -> List[PMInstruction]:
    """
    构造 PM 指令序列，每个场景独立触发一种约束。
    """
    return [
        # ── 场景1：涨停追买 ──────────────────────────────────
        PMInstruction("2024-03-01", "long",  200, 102.0, "[场景1] 正常建仓 200 股"),
        PMInstruction("2024-03-04", "long",  100, 113.3, "[场景1] 涨停追买 100 股 ← 应被拦截"),
        PMInstruction("2024-03-05", "short", 200, 115.0, "[场景1] 涨停次日正常卖出 200 股"),

        # ── 场景2：T+1 当日买当日卖 ─────────────────────────
        PMInstruction("2024-03-07", "long",  100, 110.0, "[场景2] 从零建仓 100 股"),
        PMInstruction("2024-03-07", "short", 100, 110.0, "[场景2] 当天立刻卖出 ← 应被拦截"),
        PMInstruction("2024-03-08", "short", 100, 110.0, "[场景2] T+1 次日正常卖出"),

        # ── 场景3：跌停止损 ──────────────────────────────────
        PMInstruction("2024-03-11", "long",  200, 113.0, "[场景3] 建仓 200 股"),
        PMInstruction("2024-03-12", "short", 200, 102.6, "[场景3] 跌停止损 ← 应被拦截"),
        PMInstruction("2024-03-13", "short", 200, 104.0, "[场景3] 跌停次日正常卖出"),
    ]


# ──────────────────────────────────────────────────────────────────
# 无约束执行器（只做最基础的资金校验）
# ──────────────────────────────────────────────────────────────────

class NaiveExecutor:
    """
    美股假设下的"无约束"执行器。
    接受所有 PM 指令，不检查 T+1 / 涨跌停 / 手数。
    """

    def __init__(self, cash: float):
        self.cash = cash
        self.positions: Dict[str, int] = {}
        self.trade_log: List[Dict] = []

    def execute(self, date: str, ticker: str, action: str,
                quantity: int, price: float, intent: str) -> Dict:
        cost = quantity * price
        if action == "long":
            if self.cash >= cost:
                self.cash -= cost
                self.positions[ticker] = self.positions.get(ticker, 0) + quantity
                status = "success"
                note = ""
            else:
                status = "blocked"
                note = "现金不足"
        else:  # short
            held = self.positions.get(ticker, 0)
            if held >= quantity:
                self.cash += cost * (1 - 0.001)  # 只收印花税，不管涨跌停
                self.positions[ticker] = held - quantity
                status = "success"
                note = ""
            else:
                status = "blocked"
                note = f"持仓不足（持有 {held} 股，想卖 {quantity} 股）"

        record = {
            "date": date,
            "ticker": ticker,
            "action": action,
            "quantity": quantity,
            "price": price,
            "status": status,
            "note": note,
            "intent": intent,
        }
        self.trade_log.append(record)
        return record

    def portfolio_value(self, prices: Dict[str, float]) -> float:
        stock_value = sum(
            qty * prices.get(t, 0) for t, qty in self.positions.items()
        )
        return self.cash + stock_value


# ──────────────────────────────────────────────────────────────────
# 运行实验
# ──────────────────────────────────────────────────────────────────

def run_experiment():
    instructions = build_pm_instructions()
    price_lookup = {d.date: d for d in PRICE_SERIES}

    # 初始化两个执行器
    naive = NaiveExecutor(INITIAL_CASH)
    a_share = ASharePortfolioTradeExecutor(
        initial_portfolio={"cash": INITIAL_CASH, "positions": {}}
    )

    a_share_log: List[Dict] = []

    # 按日期推进
    for day in PRICE_SERIES:
        a_share.set_date(day.date)
        a_share.set_prev_closes({TICKER: day.prev_close})

        day_instructions = [i for i in instructions if i.date == day.date]
        for inst in day_instructions:
            # 无约束执行器
            naive.execute(
                day.date, TICKER, inst.action, inst.quantity,
                inst.price, inst.intent
            )
            # A股约束执行器
            result = a_share.execute_trade(
                ticker=TICKER,
                action=inst.action,
                quantity=inst.quantity,
                price=inst.price,
                current_date=day.date,
            )
            a_share_log.append({
                "date": day.date,
                "ticker": TICKER,
                "action": inst.action,
                "quantity": inst.quantity,
                "price": inst.price,
                "status": result.get("status", "unknown"),
                "note": result.get("reason", result.get("note", "")),
                "intent": inst.intent,
            })

    # 最终持仓价值
    last_price = {TICKER: PRICE_SERIES[-1].close_price}
    naive_final = naive.portfolio_value(last_price)
    a_share_portfolio = a_share.portfolio
    a_share_positions = a_share_portfolio.get("positions", {})
    a_share_stock_val = sum(
        (v.get("quantity", 0) if isinstance(v, dict) else v) * last_price.get(t, 0)
        for t, v in a_share_positions.items()
    )
    a_share_final = a_share_portfolio.get("cash", 0) + a_share_stock_val

    return naive, naive.trade_log, a_share_log, naive_final, a_share_final, a_share_portfolio


# ──────────────────────────────────────────────────────────────────
# 输出报告
# ──────────────────────────────────────────────────────────────────

def print_report(naive_log, a_share_log, naive_final, a_share_final,
                 a_share_portfolio):
    COL = {
        "date":   10,
        "action": 6,
        "qty":    6,
        "price":  8,
        "intent": 22,
        "naive":  22,
        "ashare": 38,
    }

    def row(*cells):
        widths = list(COL.values())
        parts = [str(c).ljust(w)[:w] for c, w in zip(cells, widths)]
        print("  " + "  ".join(parts))

    def divider():
        print("  " + "-" * (sum(COL.values()) + 2 * len(COL)))

    print()
    print("=" * 80)
    print("  实验 B：A股约束触发记录  —  无约束执行器 vs A股约束执行器")
    print(f"  股票代码: {TICKER}    初始资金: ¥{INITIAL_CASH:,.0f}")
    print("=" * 80)
    print()

    # 按日期合并两个执行器的记录
    naive_by_key = {}
    for r in naive_log:
        key = (r["date"], r["action"], r["quantity"])
        naive_by_key[key] = r

    row("日期", "方向", "数量", "价格", "PM意图",
        "无约束执行器", "A股约束执行器")
    divider()

    for ar in a_share_log:
        key = (ar["date"], ar["action"], ar["quantity"])
        nr = naive_by_key.get(key, {})

        n_status = "✅ 成交" if nr.get("status") == "success" else f"❌ {nr.get('note','')}"
        a_status = (
            "✅ 成交"
            if ar["status"] == "success"
            else f"🚫 {ar['note'][:35]}"
        )

        # 如果两者结果不同，高亮这一行
        differs = (nr.get("status") == "success") != (ar["status"] == "success")
        prefix = ">>> " if differs else "    "

        row(
            ar["date"],
            ar["action"],
            str(ar["quantity"]),
            f"¥{ar['price']:.1f}",
            ar["intent"],
            n_status,
            a_status,
        )
        if differs:
            # 打印差异说明
            print(f"  {prefix}^ 差异: A股约束拦截了这笔，无约束执行器放行了")
            print()

    divider()
    print()

    # P&L 对比
    print("  ── 最终结果对比 ──────────────────────────────────────────")
    print(f"  {'':30s} {'无约束执行器':>16}  {'A股约束执行器':>16}")
    print(f"  {'最终资产总值':30s} {'¥'+f'{naive_final:,.0f}':>16}  {'¥'+f'{a_share_final:,.0f}':>16}")

    naive_pnl = naive_final - INITIAL_CASH
    a_share_pnl = a_share_final - INITIAL_CASH
    print(f"  {'相对初始资金 P&L':30s} {'+¥'+f'{naive_pnl:,.0f}' if naive_pnl>=0 else '-¥'+f'{abs(naive_pnl):,.0f}':>16}"
          f"  {'+¥'+f'{a_share_pnl:,.0f}' if a_share_pnl>=0 else '-¥'+f'{abs(a_share_pnl):,.0f}':>16}")
    naive_ret = naive_pnl / INITIAL_CASH * 100
    a_ret = a_share_pnl / INITIAL_CASH * 100
    print(f"  {'收益率':30s} {f'{naive_ret:+.2f}%':>16}  {f'{a_ret:+.2f}%':>16}")

    diff = naive_final - a_share_final
    print()
    print(f"  P&L 虚增量（无约束 - A股约束）= ¥{diff:,.2f}  ({diff/INITIAL_CASH*100:+.2f}%)")
    print()
    print("  ── A股约束执行器最终持仓 ────────────────────────────────")
    positions = a_share_portfolio.get("positions", {})
    if positions:
        for t, v in positions.items():
            qty = v.get("quantity", 0) if isinstance(v, dict) else v
            print(f"  {t}: {qty} 股")
    else:
        print("  （空仓）")
    print(f"  现金: ¥{a_share_portfolio.get('cash', 0):,.2f}")
    print()

    # 统计拦截情况
    blocked_a = [r for r in a_share_log if r["status"] != "success"]
    passed_naive = [r for r in naive_log if r["status"] == "success"]
    both_blocked = [
        r for r in a_share_log
        if r["status"] != "success"
        and naive_by_key.get((r["date"], r["action"], r["quantity"]), {}).get("status") == "success"
    ]

    print("  ── 约束触发统计 ─────────────────────────────────────────")
    print(f"  总指令数:          {len(a_share_log)}")
    print(f"  A股执行器拦截:     {len(blocked_a)} 笔")
    print(f"  无约束执行器放行:  {len(passed_naive)} 笔")
    print(f"  关键差异（A股拦截 & 无约束放行）: {len(both_blocked)} 笔")
    print()
    if both_blocked:
        print("  被A股约束拦截但无约束执行器放行的具体指令：")
        for r in both_blocked:
            print(f"    {r['date']}  {r['action']:5s} {r['quantity']:4d}股 @¥{r['price']:.1f}"
                  f"  → 拦截原因: {r['note']}")
    print()
    print("=" * 80)


if __name__ == "__main__":
    naive, naive_log, a_share_log, naive_final, a_share_final, a_share_portfolio = run_experiment()
    print_report(naive_log, a_share_log, naive_final, a_share_final, a_share_portfolio)
