# -*- coding: utf-8 -*-
"""
茅台单股回测脚本 —— A股约束引擎验证

用途：
  不需要 LLM、不需要 Agent，直接用规则策略驱动回测，
  验证 ASharePortfolioTradeExecutor 的约束是否按预期工作。

策略（故意设计成会触发各种约束）：
  1. 每天早盘：如果涨幅 > 2% → 追涨买入 100 股（会被涨停拦截）
  2. 每天收盘：如果跌幅 > 2% → 追跌卖出   （会被跌停拦截）
  3. 买入后当天立刻尝试卖出                 （会被 T+1 拦截）

运行方式：
  cd evotraders/backend
  python backtest_moutai.py

  # 离线模式（无网络/已有缓存时自动降级）：
  python backtest_moutai.py --offline
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 把 backend 目录加入路径（方便 import）
sys.path.insert(0, str(Path(__file__).parent))

from utils.a_share_constraints import ASharePortfolioTradeExecutor
from data.historical_price_manager import _CACHE_DIR, _load_from_disk_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# 模拟价格数据（离线 fallback）
# ──────────────────────────────────────────────────────────────────

def make_mock_price_data():
    """
    生成茅台模拟价格序列（10个交易日）。

    特意构造以下场景：
      Day 1 (2024-01-02): 正常交易日
      Day 2 (2024-01-03): 涨停日（涨幅 10.1%）→ 买入应被拒绝
      Day 4 (2024-01-05): 跌停日（跌幅 10.1%）→ 卖出应被拒绝
      其余：正常波动
    """
    base = 1700.0
    days = [
        # (date,    open,   close,  prev_close)
        ("2024-01-02", 1700.0, 1720.0, 1700.0),   # 正常 +1.2%
        ("2024-01-03", 1870.0, 1872.0, 1700.0),   # 涨停！开盘就涨 10%
        ("2024-01-04", 1860.0, 1850.0, 1872.0),   # 次日轻微回落
        ("2024-01-05", 1680.0, 1683.0, 1850.0),   # 跌停！开盘跌 ~10%
        ("2024-01-08", 1700.0, 1710.0, 1683.0),   # 回稳
        ("2024-01-09", 1715.0, 1730.0, 1710.0),   # 微涨 +1.2%
        ("2024-01-10", 1740.0, 1760.0, 1730.0),   # 微涨 +1.7%
        ("2024-01-11", 1750.0, 1748.0, 1760.0),   # 微跌
        ("2024-01-12", 1745.0, 1755.0, 1748.0),   # 微涨
        ("2024-01-15", 1760.0, 1775.0, 1755.0),   # 收尾正常日
    ]
    return days


def load_akshare_data(symbol: str, start: str, end: str):
    """
    尝试读取真实数据。

    优先级：
      1. akshare 在线拉取
      2. HistoricalPriceManager 的 parquet 磁盘缓存

    这样即使当前环境没装 akshare，只要之前回测缓存过茅台数据，
    也能继续做“真实数据 + 约束验证”。
    """
    try:
        import akshare as ak
        ak_symbol = symbol.split(".")[0]
        df = ak.stock_zh_a_hist(
            symbol=ak_symbol, period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return None

        df = df.rename(columns={
            "日期": "time", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "涨跌幅": "ret",
        })
        records = []
        prev_close = None
        for _, row in df.iterrows():
            open_p  = float(row["open"])
            close_p = float(row["close"])
            pc = prev_close if prev_close else open_p
            records.append((str(row["time"])[:10], open_p, close_p, pc))
            prev_close = close_p
        return records
    except Exception as e:
        logger.warning(f"akshare 拉取失败: {e}")

    cache_df = _load_from_disk_cache(symbol, start, end)
    if cache_df is None or cache_df.empty:
        safe_symbol = symbol.replace(".", "_")
        cache_candidates = sorted(_CACHE_DIR.glob(f"{safe_symbol}_*.parquet"))
        if cache_candidates:
            latest_cache = cache_candidates[-1]
            logger.info(f"未命中精确缓存，尝试复用已有缓存: {latest_cache.name}")
            try:
                import pandas as pd

                cache_df = pd.read_parquet(latest_cache)
            except Exception as cache_error:
                logger.warning(f"读取已有缓存失败: {cache_error}")
                cache_df = None

    if cache_df is None or cache_df.empty:
        return None

    logger.info(f"使用本地真实缓存数据: {symbol} {start}~{end}")
    records = []
    prev_close = None
    for date_idx, row in cache_df.iterrows():
        open_p = float(row["open"])
        close_p = float(row["close"])
        pc = prev_close if prev_close else open_p
        records.append((str(date_idx)[:10], open_p, close_p, pc))
        prev_close = close_p
    return records


# ──────────────────────────────────────────────────────────────────
# 规则策略（刻意触发各种约束）
# ──────────────────────────────────────────────────────────────────

def rule_based_decisions(
    date: str,
    symbol: str,
    open_price: float,
    prev_close: float,
    holding: int,
    cash: float,
) -> dict:
    """
    规则策略：设计成会频繁触碰A股约束的极端策略。

    Returns:
        {"action": "long"/"short"/"hold", "quantity": int, "trigger": str}
    """
    change_pct = (open_price - prev_close) / prev_close * 100

    # 规则1：涨了就追（涨幅 > 1% 就买）
    if change_pct > 1.0 and cash > open_price * 100:
        qty = min(int(cash * 0.3 / open_price // 100) * 100, 300)
        return {"action": "long", "quantity": qty or 100,
                "trigger": f"追涨（+{change_pct:.1f}%）"}

    # 规则2：跌了就卖（跌幅 > 1%，且有持仓）
    if change_pct < -1.0 and holding > 0:
        return {"action": "short", "quantity": holding,
                "trigger": f"止损（{change_pct:.1f}%）"}

    # 规则3：每次买完立刻尝试卖出（测试 T+1）
    if holding > 0:
        return {"action": "short", "quantity": 100,
                "trigger": "T+1 测试：尝试卖出持仓"}

    return {"action": "hold", "quantity": 0, "trigger": "观望"}


# ──────────────────────────────────────────────────────────────────
# 回测主循环
# ──────────────────────────────────────────────────────────────────

def run_backtest(symbol: str, offline: bool = False):
    print("\n" + "="*60)
    print(f"  A股回测：{symbol}  （A股约束引擎验证）")
    print("="*60)

    # ── 加载价格数据 ──────────────────────────────────────────────
    price_data = None
    if not offline:
        print("\n📡 正在通过 akshare 拉取真实数据...")
        price_data = load_akshare_data(symbol, "2024-01-01", "2024-01-31")

    if price_data is None:
        print("⚠️  使用内置模拟数据（含涨停/跌停/T+1场景）")
        price_data = make_mock_price_data()
    else:
        print(f"✅ 拉取到 {len(price_data)} 个交易日真实数据")

    # ── 初始化执行器 ───────────────────────────────────────────────
    initial_portfolio = {
        "cash": 500_000.0,
        "positions": {},
        "margin_requirement": 0.0,
        "margin_used": 0.0,
    }
    executor = ASharePortfolioTradeExecutor(initial_portfolio=initial_portfolio)

    # ── 回测循环 ───────────────────────────────────────────────────
    print(f"\n初始资金：¥{initial_portfolio['cash']:,.0f}\n")
    print(f"{'日期':<12} {'开盘':>8} {'收盘':>8} {'涨跌':>7} "
          f"{'决策':<20} {'结果':<12} {'持仓':>6} {'现金':>12}")
    print("-" * 90)

    daily_log = []

    for i, (date, open_p, close_p, prev_close) in enumerate(price_data):
        # 推进日期（释放过期 T+1 锁仓）
        executor.set_date(date)
        executor.set_prev_closes({symbol: prev_close})

        # 当前持仓
        holding = executor.portfolio["positions"].get(symbol, {}).get("long", 0)
        cash    = executor.portfolio["cash"]
        avail   = executor.get_available_shares(symbol)
        change  = (open_p - prev_close) / prev_close * 100

        # 策略给出决策
        dec = rule_based_decisions(date, symbol, open_p, prev_close, holding, cash)
        action   = dec["action"]
        quantity = dec["quantity"]
        trigger  = dec["trigger"]

        # 执行交易
        trade_result = {"status": "hold"}
        if action == "long" and quantity > 0:
            trade_result = executor._buy_long_position(symbol, quantity, open_p, date)
        elif action == "short" and quantity > 0:
            # 注意：传给 _sell_long_position 而不是 _open_short_position
            # （A股没有做空，"short"决策 = 卖出多头仓位）
            trade_result = executor._sell_long_position(symbol, quantity, open_p, date)

        # 汇总
        status_str = "✅ 成交" if trade_result.get("status") == "success" else \
                     f"🚫 {trade_result.get('reason', '')[:18]}"
        new_holding = executor.portfolio["positions"].get(symbol, {}).get("long", 0)
        new_cash    = executor.portfolio["cash"]

        # 日志
        print(f"{date:<12} {open_p:>8.1f} {close_p:>8.1f} {change:>+6.1f}%"
              f" {trigger:<20} {status_str:<18} {new_holding:>5}股"
              f" ¥{new_cash:>10,.0f}")

        daily_log.append({
            "date": date, "open": open_p, "close": close_p,
            "action": action, "quantity": quantity,
            "status": trade_result.get("status"),
            "reason": trade_result.get("reason", ""),
            "holding": new_holding, "cash": new_cash,
        })

    # ── 结果汇总 ───────────────────────────────────────────────────
    final_holding = executor.portfolio["positions"].get(symbol, {}).get("long", 0)
    final_cash    = executor.portfolio["cash"]
    last_price    = price_data[-1][2]  # 最后一天收盘价
    portfolio_val = final_cash + final_holding * last_price
    pnl           = portfolio_val - initial_portfolio["cash"]
    pnl_pct       = pnl / initial_portfolio["cash"] * 100

    print("\n" + "="*60)
    print("  回测结果")
    print("="*60)
    print(f"  期末现金：      ¥{final_cash:>12,.2f}")
    print(f"  持仓 {final_holding} 股 × ¥{last_price:.2f}：  "
          f"¥{final_holding * last_price:>10,.2f}")
    print(f"  总资产：        ¥{portfolio_val:>12,.2f}")
    print(f"  盈亏：          ¥{pnl:>+12,.2f}  ({pnl_pct:+.2f}%)")

    # ── 约束统计 ───────────────────────────────────────────────────
    summary = executor.get_constraint_summary()
    print(f"\n{'='*60}")
    print("  A股约束引擎拦截统计")
    print(f"{'='*60}")
    print(f"  总约束事件：    {summary['total_constraint_events']} 次")
    print(f"  实际拦截：      {summary['blocked_orders']} 次")
    by_type = summary["blocked_by_type"]
    print(f"    涨停拦截：    {by_type['limit_up']} 次  （买不进去）")
    print(f"    跌停拦截：    {by_type['limit_down']} 次  （卖不出去）")
    print(f"    T+1 锁仓：   {by_type['t_plus_1']} 次  （当日买入不能卖）")
    print(f"    做空禁止：    {by_type['short_ban']} 次")
    print(f"    资金不足：    {by_type['insufficient_cash']} 次")

    if summary["blocked_orders"] > 0:
        print("\n  拦截明细：")
        for ev in summary["constraint_log"]:
            if ev["blocked"]:
                print(f"    {ev['date']} {ev['action']:4} {ev['symbol']}: "
                      f"{ev['reason'][:50]}")

    print()
    return daily_log, summary


# ──────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="茅台 A股回测（约束引擎验证）")
    parser.add_argument(
        "--offline", action="store_true",
        help="离线模式：使用内置模拟数据，不联网"
    )
    parser.add_argument(
        "--symbol", default="600519.SH",
        help="股票代码，默认茅台 600519.SH"
    )
    args = parser.parse_args()

    run_backtest(symbol=args.symbol, offline=args.offline)
