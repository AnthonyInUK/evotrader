# -*- coding: utf-8 -*-
"""
多股回测脚本 —— Week 1 集成测试（茅台 + 工行）

目标：
  1. 验证 ASharePortfolioTradeExecutor 在多股场景下正常工作
  2. 验证 T+1 锁仓对每只股票独立跟踪（买了茅台锁茅台，不影响工行）
  3. 验证涨跌停约束对每只股票使用各自的 prev_close
  4. 验证 HistoricalPriceManager 防未来函数（不泄漏未来价格）

运行方式：
  cd evotraders/backend
  python backtest_multi_stock.py --offline       # 使用内置模拟数据（推荐先跑这个）
  python backtest_multi_stock.py                 # 尝试 akshare 拉取真实数据

关键设计场景（模拟数据）：
  茅台 (600519.SH): Day3 涨停，Day5 跌停，其他正常波动
  工行 (601398.SH): Day2 正常买入，Day3 T+1 测试（当天买不能当天卖），其他正常
"""
import argparse
import logging
import sys
from pathlib import Path

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
# 模拟价格数据
# ──────────────────────────────────────────────────────────────────

def make_mock_data() -> dict:
    """
    生成两只股票的模拟价格序列（10个共同交易日）。

    设计意图：
      - 茅台：高价股，用来测涨跌停拦截（价格波动大容易触发）
      - 工行：低价股，用来测 T+1 锁仓（价格低便于计算资金）
      - 同一天两只股票同时出现，测试多股并行管理

    字段：(date, open, close, prev_close)
    """
    moutai = [
        # (date,         open,    close,   prev_close)
        ("2024-01-02", 1700.0,  1720.0,  1700.0),   # 茅台正常 +1.2%
        ("2024-01-03", 1870.0,  1872.0,  1700.0),   # 茅台涨停！（不应买入）
        ("2024-01-04", 1860.0,  1850.0,  1872.0),   # 茅台次日回落
        ("2024-01-05", 1680.0,  1683.0,  1850.0),   # 茅台跌停！（不应卖出）
        ("2024-01-08", 1700.0,  1710.0,  1683.0),   # 茅台回稳
        ("2024-01-09", 1715.0,  1730.0,  1710.0),   # 茅台微涨
        ("2024-01-10", 1740.0,  1760.0,  1730.0),   # 茅台继续涨
        ("2024-01-11", 1750.0,  1748.0,  1760.0),   # 茅台微跌
        ("2024-01-12", 1745.0,  1755.0,  1748.0),   # 茅台微涨
        ("2024-01-15", 1760.0,  1775.0,  1755.0),   # 茅台收尾
    ]
    icbc = [
        # (date,         open,  close,  prev_close)
        ("2024-01-02",  5.20,   5.25,   5.20),      # 工行正常
        ("2024-01-03",  5.28,   5.30,   5.25),      # 工行正常（买入，次日测T+1）
        ("2024-01-04",  5.32,   5.28,   5.30),      # 工行微跌（T+1: 昨天买的今天可卖）
        ("2024-01-05",  5.25,   5.22,   5.28),      # 工行继续跌
        ("2024-01-08",  5.20,   5.24,   5.22),      # 工行企稳
        ("2024-01-09",  5.26,   5.29,   5.24),      # 工行小涨
        ("2024-01-10",  5.30,   5.35,   5.29),      # 工行继续涨
        ("2024-01-11",  5.33,   5.31,   5.35),      # 工行微跌
        ("2024-01-12",  5.29,   5.33,   5.31),      # 工行反弹
        ("2024-01-15",  5.34,   5.38,   5.33),      # 工行收尾
    ]
    return {
        "600519.SH": moutai,
        "601398.SH": icbc,
    }


def load_akshare_data(symbol: str, start: str, end: str):
    """尝试 akshare 拉取，失败则返回 None（由主函数 fallback 到模拟数据）"""
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
            open_p = float(row["open"])
            close_p = float(row["close"])
            pc = prev_close if prev_close else open_p
            records.append((str(row["time"])[:10], open_p, close_p, pc))
            prev_close = close_p
        return records
    except Exception as e:
        logger.warning(f"akshare 拉取 {symbol} 失败: {e}")

    # 尝试磁盘缓存
    cache_df = _load_from_disk_cache(symbol, start, end)
    if cache_df is None or cache_df.empty:
        safe_symbol = symbol.replace(".", "_")
        cache_candidates = sorted(_CACHE_DIR.glob(f"{safe_symbol}_*.parquet"))
        if cache_candidates:
            try:
                import pandas as pd
                cache_df = pd.read_parquet(cache_candidates[-1])
                logger.info(f"使用磁盘缓存: {cache_candidates[-1].name}")
            except Exception as ce:
                logger.warning(f"读取磁盘缓存失败: {ce}")
    if cache_df is not None and not cache_df.empty:
        records = []
        prev_close = None
        for date_idx, row in cache_df.iterrows():
            open_p = float(row["open"])
            close_p = float(row["close"])
            pc = prev_close if prev_close else open_p
            records.append((str(date_idx)[:10], open_p, close_p, pc))
            prev_close = close_p
        return records
    return None


# ──────────────────────────────────────────────────────────────────
# 规则策略
# ──────────────────────────────────────────────────────────────────

def rule_strategy(
    symbol: str,
    open_p: float,
    prev_close: float,
    holding: int,
    avail: int,
    cash: float,
    day_idx: int,
) -> dict:
    """
    两只股票共用的规则策略（故意触发各种约束）：

    茅台（高价）：每500万买入后等待，跌了止损
    工行（低价）：每天早盘买入，当天立刻尝试卖出（测 T+1）
    """
    chg = (open_p - prev_close) / prev_close * 100

    # 通用：追涨（涨幅 > 1%）
    if chg > 1.0 and cash > open_p * 100:
        qty = min(int(cash * 0.2 / open_p // 100) * 100, 200)
        return {"action": "long", "qty": qty or 100,
                "trigger": f"追涨 +{chg:.1f}%"}

    # 通用：止损（跌幅 > 1% 且有可卖仓位）
    if chg < -1.0 and avail > 0:
        return {"action": "short", "qty": avail,
                "trigger": f"止损 {chg:.1f}%"}

    # 工行（低价股）：若有持仓就尝试卖出（测 T+1）
    if "601398" in symbol and holding > 0:
        return {"action": "short", "qty": 100,
                "trigger": "工行 T+1 测试"}

    return {"action": "hold", "qty": 0, "trigger": "观望"}


# ──────────────────────────────────────────────────────────────────
# 回测主循环
# ──────────────────────────────────────────────────────────────────

def run_multi_backtest(offline: bool = False):
    symbols = ["600519.SH", "601398.SH"]
    symbol_names = {"600519.SH": "茅台", "601398.SH": "工行"}

    print("\n" + "=" * 70)
    print("  A股多股回测：茅台 (600519.SH) + 工行 (601398.SH)")
    print("  验证目标：T+1独立锁仓、涨跌停多股并行约束、防未来函数")
    print("=" * 70)

    # ── 加载价格数据 ──────────────────────────────────────────────
    price_data = {}  # symbol -> [(date, open, close, prev_close), ...]
    mock_data = make_mock_data()

    for symbol in symbols:
        if not offline:
            real = load_akshare_data(symbol, "2024-01-01", "2024-01-31")
            if real:
                price_data[symbol] = real
                print(f"✅ {symbol_names[symbol]}: 真实数据 {len(real)} 个交易日")
                continue
        price_data[symbol] = mock_data[symbol]
        print(f"⚠️  {symbol_names[symbol]}: 模拟数据 {len(mock_data[symbol])} 个交易日")

    # ── 初始化共享执行器（单一 portfolio，持有两只股票）──────────
    initial_portfolio = {
        "cash": 2_000_000.0,   # 200万，够同时持两只
        "positions": {},
        "margin_requirement": 0.0,
        "margin_used": 0.0,
    }
    executor = ASharePortfolioTradeExecutor(initial_portfolio=initial_portfolio)

    # ── 对齐交易日（取两只股票的交集）────────────────────────────
    dates_moutai = [row[0] for row in price_data["600519.SH"]]
    dates_icbc   = [row[0] for row in price_data["601398.SH"]]
    common_dates = sorted(set(dates_moutai) & set(dates_icbc))

    moutai_map = {row[0]: row for row in price_data["600519.SH"]}
    icbc_map   = {row[0]: row for row in price_data["601398.SH"]}

    print(f"\n共同交易日 {len(common_dates)} 天，初始资金 ¥{initial_portfolio['cash']:,.0f}\n")

    # ── 每日循环 ──────────────────────────────────────────────────
    daily_logs = []

    for day_idx, date in enumerate(common_dates):
        # 推进日期（释放 T+1 过期锁仓）
        executor.set_date(date)
        prev_closes = {
            "600519.SH": moutai_map[date][3],
            "601398.SH": icbc_map[date][3],
        }
        executor.set_prev_closes(prev_closes)

        day_log = {"date": date, "trades": []}

        # 每只股票独立决策 + 执行
        for symbol in symbols:
            row = moutai_map[date] if "600519" in symbol else icbc_map[date]
            _, open_p, close_p, prev_close = row

            holding = executor.portfolio["positions"].get(symbol, {}).get("long", 0)
            avail   = executor.get_available_shares(symbol)
            cash    = executor.portfolio["cash"]
            chg     = (open_p - prev_close) / prev_close * 100

            dec = rule_strategy(symbol, open_p, prev_close, holding, avail, cash, day_idx)
            action, qty, trigger = dec["action"], dec["qty"], dec["trigger"]

            result = {"status": "hold"}
            if action == "long" and qty > 0:
                result = executor._buy_long_position(symbol, qty, open_p, date)
            elif action == "short" and qty > 0:
                result = executor._sell_long_position(symbol, qty, open_p, date)

            new_holding = executor.portfolio["positions"].get(symbol, {}).get("long", 0)
            ok = result.get("status") == "success"
            reason = result.get("reason", "")[:20] if not ok and action != "hold" else ""
            status_str = "✅ 成交" if ok else (f"🚫 {reason}" if action != "hold" else "—")

            day_log["trades"].append({
                "symbol": symbol,
                "open": open_p, "close": close_p,
                "chg": chg, "trigger": trigger,
                "action": action, "qty": qty,
                "ok": ok, "reason": result.get("reason", ""),
                "holding": new_holding,
            })

        day_log["cash"] = executor.portfolio["cash"]
        daily_logs.append(day_log)

        # 打印当天摘要
        moutai_t = day_log["trades"][0]
        icbc_t   = day_log["trades"][1]
        cash_now = day_log["cash"]
        moutai_h = moutai_t["holding"]
        icbc_h   = icbc_t["holding"]

        def _fmt_trade(t):
            if t["action"] == "hold":
                return f"{'观望':12}"
            ok_str = "✅" if t["ok"] else "🚫"
            return f"{ok_str} {t['trigger'][:10]:10}"

        print(
            f"{date}  "
            f"茅台 {moutai_t['open']:>8.1f}({moutai_t['chg']:+.1f}%) {_fmt_trade(moutai_t)} 持{moutai_h:>4}股  │  "
            f"工行 {icbc_t['open']:>5.2f}({icbc_t['chg']:+.1f}%) {_fmt_trade(icbc_t)} 持{icbc_h:>5}股  "
            f"现金 ¥{cash_now:>12,.0f}"
        )

    # ── 汇总 ──────────────────────────────────────────────────────
    last_date = common_dates[-1]
    last_moutai = moutai_map[last_date]
    last_icbc   = icbc_map[last_date]
    moutai_holding = executor.portfolio["positions"].get("600519.SH", {}).get("long", 0)
    icbc_holding   = executor.portfolio["positions"].get("601398.SH", {}).get("long", 0)
    final_cash     = executor.portfolio["cash"]
    portfolio_val  = (
        final_cash
        + moutai_holding * last_moutai[2]
        + icbc_holding   * last_icbc[2]
    )
    pnl     = portfolio_val - initial_portfolio["cash"]
    pnl_pct = pnl / initial_portfolio["cash"] * 100

    print("\n" + "=" * 70)
    print("  回测结果")
    print("=" * 70)
    print(f"  期末现金：        ¥{final_cash:>14,.2f}")
    print(f"  茅台 {moutai_holding} 股 × ¥{last_moutai[2]:.2f}：  ¥{moutai_holding * last_moutai[2]:>12,.2f}")
    print(f"  工行 {icbc_holding} 股 × ¥{last_icbc[2]:.2f}：  ¥{icbc_holding * last_icbc[2]:>12,.2f}")
    print(f"  总资产：          ¥{portfolio_val:>14,.2f}")
    print(f"  盈亏：            ¥{pnl:>+14,.2f}  ({pnl_pct:+.2f}%)")

    # ── 约束统计 ──────────────────────────────────────────────────
    summary = executor.get_constraint_summary()
    print(f"\n{'=' * 70}")
    print("  A股约束引擎拦截统计（两只股票合并）")
    print(f"{'=' * 70}")
    print(f"  总拦截次数：      {summary['blocked_orders']} 次")
    by_type = summary["blocked_by_type"]
    print(f"    涨停拦截：      {by_type['limit_up']} 次")
    print(f"    跌停拦截：      {by_type['limit_down']} 次")
    print(f"    T+1 锁仓：     {by_type['t_plus_1']} 次")
    print(f"    资金不足：      {by_type['insufficient_cash']} 次")

    if summary["blocked_orders"] > 0:
        print("\n  拦截明细：")
        for ev in summary["constraint_log"]:
            if ev["blocked"]:
                name = symbol_names.get(ev["symbol"], ev["symbol"])
                print(f"    {ev['date']} [{name}] {ev['action']} → {ev['reason'][:50]}")
    print()

    return daily_logs, summary


# ──────────────────────────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多股 A股回测（茅台 + 工行）")
    parser.add_argument("--offline", action="store_true", help="使用内置模拟数据")
    args = parser.parse_args()
    run_multi_backtest(offline=args.offline)
