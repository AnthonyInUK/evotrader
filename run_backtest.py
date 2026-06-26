# -*- coding: utf-8 -*-
"""
直接运行回测，绕过 WebSocket gateway
用法: python run_backtest.py --start 2024-01-02 --end 2024-01-05
"""
# ── 代理绕过：必须在所有其他 import 之前 ─────────────────────────────────────
# 黑豹加速器 / Clash 等全局代理会拦截国内流量（DashScope、东方财富、同花顺），
# 导致 aiohttp ServerDisconnectedError 和 akshare SSL 握手失败。
# 在此统一设置 NO_PROXY，让 Python 进程直连国内服务。
# Claude Code 是独立进程，不受这里的环境变量影响。
import os as _os

from backend.utils.patch_agentscope import apply_patches

apply_patches()

_DOMESTIC_NO_PROXY = ",".join([
    # 阿里云 DashScope（LLM API）
    "dashscope.aliyuncs.com",
    "*.aliyuncs.com",
    "aliyuncs.com",
    # 东方财富（akshare 行情、现金流数据）
    "eastmoney.com",
    "*.eastmoney.com",
    "emweb.securities.eastmoney.com",
    # 同花顺（akshare 财务摘要）
    "10jqka.com.cn",
    "*.10jqka.com.cn",
    # 新浪财经
    "finance.sina.com.cn",
    "*.sina.com.cn",
    # 本地 / 局域网
    "localhost",
    "127.0.0.1",
])
_os.environ["NO_PROXY"] = _DOMESTIC_NO_PROXY
_os.environ["no_proxy"] = _DOMESTIC_NO_PROXY  # 小写兼容 requests/urllib
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import argparse
import csv
import logging
import math
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(override=True)
sys.path.insert(0, str(Path(__file__).parent))

from backend.data.historical_price_manager import HistoricalPriceManager
from backend.core.pipeline import TradingPipeline
from backend.core.scheduler import BacktestScheduler
from backend.agents import AnalystAgent, PMAgent, RiskAgent
from backend.config.constants import ANALYST_TYPES
from backend.config.env_config import get_env_float, get_env_list, get_env_int
from backend.llm.models import get_agent_formatter, get_agent_model
from backend.utils.settlement import SettlementCoordinator
from backend.services.storage import StorageService
from backend.main import create_local_toolkit

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger("run_backtest")

_REQUIRED_EXPERIMENT_INDICATORS = {
    "technical_trend_snapshot_v1",
    "technical_momentum_risk_v1",
}


def _format_price_map(prices):
    """Render open/close price dict without crashing on missing values."""
    rendered = {}
    for ticker, price in prices.items():
        rendered[ticker] = f"${price:.2f}" if price is not None else "N/A"
    return rendered


def _experiment_indicators_ready(config_name: str) -> tuple[bool, set[str], Path]:
    """Check fixed indicator preparation before a paid LLM backtest starts."""
    code_path = Path(
        _os.getenv(
            "EXPERIMENT_CODE_PATH",
            str(Path(config_name) / "experiment_code" / "execute_code_validated.jsonl"),
        )
    )
    if not code_path.exists():
        return False, set(), code_path

    import json as _json

    found: set[str] = set()
    for line in code_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        indicator_id = record.get("indicator_id")
        if indicator_id:
            found.add(str(indicator_id))
    return _REQUIRED_EXPERIMENT_INDICATORS.issubset(found), found, code_path


def _print_prepare_hint(
    config_name: str,
    start_date: str,
    end_date: str,
    tickers: list[str],
    code_path: Path,
    found: set[str],
) -> None:
    missing = sorted(_REQUIRED_EXPERIMENT_INDICATORS - found)
    print("\n❌ 固定指标未准备好，已停止回测以避免浪费 LLM 成本。")
    print(f"   Config: {config_name}")
    print(f"   Code path: {code_path}")
    print(f"   Missing indicators: {missing or 'file missing'}")
    print("\n请先运行：")
    print(
        "python scripts/prepare_experiment_code.py "
        f"{config_name} "
        f"--start {start_date} "
        f"--end {end_date} "
        f"--date {end_date} "
        f"--tickers {','.join(tickers)}"
    )
    print("\n如确实要跳过固定指标检查，可设置 SKIP_EXPERIMENT_PREFLIGHT=1。\n")


def _install_embedding_arrearage_guard():
    """
    让 DashScope embedding 欠费时进程立即停止，而不是被 ReMe 静默吞掉。

    ReMe 的 record()/retrieve() 用 `except Exception` 捕获所有异常并只记日志，
    所以 embedding 欠费(Arrearage)时记忆会无声写不进去、退化成无记忆基线。
    这里给 flowllm 的 embedding 调用打补丁：检测到欠费类错误就抛 SystemExit
    (属于 BaseException，不会被 `except Exception` 捕获)，从而中断整个回测。
    """
    try:
        from flowllm.core.embedding_model.openai_compatible_embedding_model import (
            OpenAICompatibleEmbeddingModel,
        )
    except ImportError:
        return  # flowllm 不在则无需保护

    if getattr(OpenAICompatibleEmbeddingModel, "_arrearage_guard_installed", False):
        return

    _orig = OpenAICompatibleEmbeddingModel._async_get_embeddings
    _signals = ("arrearage", "overdue", "good standing", "insufficient balance")

    async def _guarded(self, input_text):
        try:
            return await _orig(self, input_text)
        except Exception as e:  # noqa: BLE001 - 仅用于识别欠费后升级为 SystemExit
            msg = str(getattr(e, "args", e)).lower()
            if any(s in msg for s in _signals):
                raise SystemExit(
                    "\n❌ DashScope embedding 欠费/额度耗尽，记忆无法写入。"
                    "\n   已主动中断回测，避免静默退化成无记忆基线。"
                    "\n   请到 bailian.console.aliyun.com 充值并关闭『仅使用免费额度』后重跑。\n"
                ) from e
            raise

    OpenAICompatibleEmbeddingModel._async_get_embeddings = _guarded
    OpenAICompatibleEmbeddingModel._arrearage_guard_installed = True


def create_agents(config_name, initial_cash, margin_requirement, use_long_term_memory=False):
    analysts, memories = [], []

    # ── 长期记忆初始化 ─────────────────────────────────────────────────────────
    embedding_model = None
    if use_long_term_memory:
        from agentscope.memory import ReMeTaskLongTermMemory
        from agentscope.embedding import DashScopeTextEmbedding
        dashscope_api_key = _os.getenv("DASHSCOPE_API_KEY", "")
        embedding_model = DashScopeTextEmbedding(
            api_key=dashscope_api_key,
            model_name="text-embedding-v3",
        )
        _install_embedding_arrearage_guard()
    # ──────────────────────────────────────────────────────────────────────────

    for analyst_type in ANALYST_TYPES:
        ltm = None
        if use_long_term_memory and embedding_model is not None:
            import json as _json
            # 按 config 隔离记忆库：多 seed A/B 时每个 seed 必须用独立的空向量库，
            # 否则共享记忆会污染"运行间方差"的估计（破坏独立重复假设）。
            _store_dir = str(
                Path(__file__).parent / "backend" / "data"
                / "reme_vector_store" / config_name
            )
            ltm = ReMeTaskLongTermMemory(
                agent_name=f"{analyst_type}_analyst",
                user_name=f"evotraders_{analyst_type}",
                model=get_agent_model(analyst_type),
                embedding_model=embedding_model,
                **{
                    "vector_store.default.backend": "local",
                    "vector_store.default.params": _json.dumps({"store_dir": _store_dir}),
                },
            )
            memories.append(ltm)

        agent = AnalystAgent(
            analyst_type=analyst_type,
            toolkit=create_local_toolkit(analyst_type),
            model=get_agent_model(analyst_type),
            formatter=get_agent_formatter(analyst_type),
            config={"initial_cash": initial_cash,
                    "margin_requirement": margin_requirement},
            long_term_memory=ltm,
        )
        analysts.append(agent)

    risk_manager = RiskAgent(
        model=get_agent_model("risk_manager"),
        formatter=get_agent_formatter("risk_manager"),
        config={"initial_cash": initial_cash,
                "margin_requirement": margin_requirement},
    )
    pm = PMAgent(
        model=get_agent_model("portfolio_manager"),
        formatter=get_agent_formatter("portfolio_manager"),
        initial_cash=initial_cash,
        config={"initial_cash": initial_cash,
                "margin_requirement": margin_requirement},
    )
    return analysts, risk_manager, pm, memories


async def run(
    start_date,
    end_date,
    config_name,
    reset=False,
    use_long_term_memory=False,
    tickers_override=None,
):
    _os.environ["EVOTRADERS_CONFIG_NAME"] = config_name
    _os.environ.setdefault(
        "EXPERIMENT_CODE_PATH",
        str(Path(config_name) / "experiment_code" / "execute_code_validated.jsonl"),
    )

    tickers = tickers_override or get_env_list("TICKERS", ["AAPL", "MSFT"])
    initial_cash = get_env_float("INITIAL_CASH", 100000.0)
    margin_requirement = get_env_float("MARGIN_REQUIREMENT", 0.5)

    if not _os.getenv("SKIP_EXPERIMENT_PREFLIGHT", "").strip():
        ready, found, code_path = _experiment_indicators_ready(config_name)
        if not ready:
            _print_prepare_hint(
                config_name=config_name,
                start_date=start_date,
                end_date=end_date,
                tickers=tickers,
                code_path=code_path,
                found=found,
            )
            return

    print(f"\n{'='*50}")
    print(f"  EvoTraders 本地回测")
    print(f"  Tickers : {tickers}")
    print(f"  期间    : {start_date} → {end_date}")
    print(f"  初始资金: ¥{initial_cash:,.0f}")
    if reset:
        print(f"  模式    : 全新回测（忽略历史状态）")
    print(f"{'='*50}\n")

    # ── 计算 warmup 起始日期（为技术指标提供历史数据）────────────────
    from datetime import datetime as _dt, timedelta as _td
    _warmup_days = 150  # 覆盖 analyze_trend_following 的 120 天窗口 + 节假日余量
    warmup_start = (_dt.strptime(start_date, "%Y-%m-%d") - _td(days=_warmup_days)).strftime("%Y-%m-%d")

    # 加载价格数据：从 warmup_start 开始，一次拉取覆盖历史+回测区间
    # 避免分两次调 akshare 触发东方财富的频率限制
    price_mgr = HistoricalPriceManager()
    price_mgr.subscribe(tickers)
    price_mgr.preload_data(warmup_start, end_date)

    # 存储服务
    storage = StorageService(
        dashboard_dir=Path(config_name) / "team_dashboard",
        initial_cash=initial_cash,
        config_name=config_name,
    )
    if reset:
        storage.initialize_empty_dashboard()

    # 创建 agents
    if use_long_term_memory:
        print("  🧠 长期记忆：已启用（ReMeTaskLongTermMemory + DashScope embedding）")
    analysts, risk_manager, pm, memories = create_agents(
        config_name, initial_cash, margin_requirement,
        use_long_term_memory=use_long_term_memory,
    )
    # --reset: 清除所有磁盘状态，从全新组合开始
    if reset:
        storage.save_internal_state({})  # 清空 settlement 的持久化状态
    else:
        portfolio_state = storage.load_portfolio_state()
        pm.load_portfolio_state(portfolio_state)

    settlement = SettlementCoordinator(
        storage=storage,
        initial_capital=initial_cash,
    )

    # ── 启动长期记忆上下文（必须在 pipeline 运行前 __aenter__）──────────────
    import contextlib as _contextlib
    _ltm_stack = _contextlib.AsyncExitStack()
    await _ltm_stack.__aenter__()
    for _ltm in memories:
        await _ltm_stack.enter_async_context(_ltm)
    # ──────────────────────────────────────────────────────────────────────────

    pipeline = TradingPipeline(
        analysts=analysts,
        risk_manager=risk_manager,
        portfolio_manager=pm,
        settlement_coordinator=settlement,
        max_comm_cycles=get_env_int("MAX_COMM_CYCLES", 1),
        config_name=config_name,
    )

    # 按交易日逐日运行
    scheduler = BacktestScheduler(
        start_date=start_date,
        end_date=end_date,
        trading_calendar="NYSE",
        delay_between_days=0,
    )
    trading_dates = scheduler.get_trading_dates()
    print(f"交易日共 {len(trading_dates)} 天: {trading_dates}\n")
    print(f"🔥 价格缓存预热: {warmup_start} → {end_date}（已由 preload_data 完成）")
    _data_ok = True
    for ticker in tickers:
        df = price_mgr._price_cache.get(ticker)
        n = len(df) if df is not None else 0
        status = "✅" if n >= 60 else "⚠️ 不足60条，技术指标可能失效"
        print(f"   {ticker}: {n} 条历史价格已缓存 {status}")
        if n < 60:
            _data_ok = False
    if not _data_ok:
        print("\n❌ 数据不足，终止回测。请检查 warmup_days 设置或数据源。\n")
        return
    print()

    # ── 逐日追加写入 nav_curve.csv（支持分段续跑）────────────────────────
    csv_path = Path(config_name) / "nav_curve.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    _nav_fields = ["date", "nav", "cash", "positions_value"]

    if reset and csv_path.exists():
        csv_path.unlink()  # --reset 时清空旧数据

    if not csv_path.exists():
        with open(csv_path, "w", newline="", encoding="utf-8") as _f:
            csv.DictWriter(_f, fieldnames=_nav_fields).writeheader()

    # 读取已有日期，避免重复写入
    _existing_dates = set()
    if csv_path.exists():
        with open(csv_path, "r", encoding="utf-8") as _f:
            for row in csv.DictReader(_f):
                _existing_dates.add(row["date"])

    nav_records = []  # 仅记录本次运行的数据，供结束摘要使用

    # ── 买入持有基准（跨段持久化）────────────────────────────────────────
    import json as _json
    _bm_path = Path(config_name) / "benchmark_state.json"
    if reset and _bm_path.exists():
        _bm_path.unlink()

    if _bm_path.exists():
        _bm_state = _json.loads(_bm_path.read_text())
        benchmark_shares   = _bm_state["shares"]        # {ticker: shares}
        benchmark_cash_rem = _bm_state["cash_remainder"] # 买不整股剩余的现金
    else:
        benchmark_shares   = {}
        benchmark_cash_rem = 0.0

    _last_close_prices = {}  # 每天更新，摘要用

    for date in trading_dates:
        price_mgr.set_date(date)
        _open  = {t: price_mgr.get_open_price(t)  for t in tickers}
        _close = {t: price_mgr.get_close_price(t) for t in tickers}

        # 只保留开盘价和收盘价都有效的 ticker；无数据的跳过当日
        valid_tickers = [t for t in tickers if _open.get(t) and _close.get(t)]
        open_prices  = {t: _open[t]  for t in valid_tickers}
        close_prices = {t: _close[t] for t in valid_tickers}

        missing = set(tickers) - set(valid_tickers)
        if missing:
            print(f"   ⚠️  {date} 无价格数据，跳过: {missing}")

        if not valid_tickers:
            print(f"\n📅 {date}  — 全部 ticker 无数据，跳过")
            continue

        print(f"\n📅 {date}")
        print(f"   开盘: {_format_price_map(open_prices)}")

        result = await pipeline.run_cycle(
            tickers=valid_tickers,
            date=date,
            prices=open_prices,
            close_prices=close_prices,
        )

        # 打印 PM 决策（今日操作）+ 实际持仓
        decisions = result.get("pm_decisions", {})
        portfolio_now = result.get("portfolio", {})
        positions_now = portfolio_now.get("positions", {})
        if decisions:
            for ticker, d in decisions.items():
                action = d.get("action", "hold").upper()
                qty    = d.get("quantity", 0)
                conf   = d.get("confidence", 0)
                # 显示今日操作，以及操作后的实际持仓
                pos = positions_now.get(ticker, {})
                held = pos.get("long", pos.get("quantity", 0)) if isinstance(pos, dict) else 0
                print(f"   📊 {ticker}: 今日操作={action} {qty}股 (信心{conf}%) | 实际持仓={held}股")
        else:
            print("   📊 无决策")

        # 打印实际执行的交易
        trades = result.get("executed_trades", [])
        if trades:
            print(f"   💰 执行: {[(t['ticker'], t['action'], t['quantity']) for t in trades]}")

        # 打印当日结束后的组合状态
        portfolio = result.get("portfolio", {})
        cash = portfolio.get("cash", 0)
        positions = portfolio.get("positions", {})
        print(f"   💼 现金: ¥{cash:,.2f}")
        if positions:
            for ticker, pos in positions.items():
                if isinstance(pos, dict):
                    long_qty  = pos.get("long", pos.get("quantity", 0))
                    short_qty = pos.get("short", 0)
                    cost      = pos.get("long_cost_basis", pos.get("cost_basis", 0))
                    open_p    = open_prices.get(ticker, 0)
                    mkt_val   = long_qty * open_p if open_p else 0
                    pnl_str   = ""
                    if cost > 0 and long_qty > 0:
                        pnl = (open_p - cost) / cost * 100
                        pnl_str = f", 浮盈 {pnl:+.1f}%"
                    short_str = f", 空头 {short_qty}股" if short_qty else ""
                    print(
                        f"   📈 {ticker}: 多头 {long_qty}股"
                        f" (市值 ¥{mkt_val:,.0f}, 成本 ¥{cost:.2f}{pnl_str}){short_str}"
                    )
                else:
                    print(f"   📈 {ticker}: {pos}股")

        # ── 用收盘价算当日总资产（NAV）──────────────────────────────────────
        positions_value = 0.0
        for ticker, pos in positions.items():
            close_p = close_prices.get(ticker) or 0
            if isinstance(pos, dict):
                long_qty = pos.get("long", pos.get("quantity", 0))
            else:
                long_qty = int(pos)
            positions_value += long_qty * close_p
        nav = cash + positions_value
        row = {"date": date, "nav": nav, "cash": cash, "positions_value": positions_value}
        nav_records.append(row)
        # 立即追加写入磁盘，中途失败不丢数据（跳过已存在日期）
        if date not in _existing_dates:
            with open(csv_path, "a", newline="", encoding="utf-8") as _f:
                csv.DictWriter(_f, fieldnames=_nav_fields).writerow(row)
            _existing_dates.add(date)
        _last_close_prices = close_prices

        # 基准：第一次出现有效价格时买入
        if not benchmark_shares and valid_tickers:
            per_cash = initial_cash / len(valid_tickers)
            benchmark_cash_rem = 0.0
            for t in valid_tickers:
                shares_bought = int(per_cash / open_prices[t])
                benchmark_shares[t] = shares_bought
                benchmark_cash_rem += per_cash - shares_bought * open_prices[t]
            _bm_path.parent.mkdir(parents=True, exist_ok=True)
            _bm_path.write_text(_json.dumps(
                {"shares": benchmark_shares, "cash_remainder": benchmark_cash_rem}
            ))
            print(f"   📌 基准买入: { {t: benchmark_shares[t] for t in valid_tickers} }")

        daily_ret = nav / initial_cash - 1
        print(f"   📊 收盘总资产: ¥{nav:,.2f}  (累计收益 {daily_ret:+.2%})")

    # ── 回测结束：计算绩效指标 ────────────────────────────────────────────
    print(f"\n{'='*50}")
    print("  回测完成 · 绩效摘要")
    print(f"{'='*50}")

    if len(nav_records) >= 2:
        navs = [r["nav"] for r in nav_records]

        # 总收益率
        total_return = navs[-1] / initial_cash - 1

        # 每日收益率序列
        daily_returns = [
            navs[i] / navs[i - 1] - 1 for i in range(1, len(navs))
        ]

        # 最大回撤
        peak = navs[0]
        max_dd = 0.0
        for v in navs:
            peak = max(peak, v)
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)

        # Sharpe Ratio（假设无风险利率 = 0，年化 252 交易日）
        n = len(daily_returns)
        mean_ret = sum(daily_returns) / n
        variance = sum((r - mean_ret) ** 2 for r in daily_returns) / n
        std_ret = math.sqrt(variance) if variance > 0 else 0
        sharpe = (mean_ret / std_ret * math.sqrt(252)) if std_ret > 0 else float("nan")

        print(f"  初始资金  : ¥{initial_cash:>12,.2f}")
        print(f"  最终资产  : ¥{navs[-1]:>12,.2f}")
        print(f"  总收益率  : {total_return:>+12.2%}")
        print(f"  最大回撤  : {max_dd:>12.2%}")
        print(f"  Sharpe    : {sharpe:>12.4f}")

        # ── 买入持有基准对比 ───────────────────────────────────────────────
        if benchmark_shares and _last_close_prices:
            bm_nav = benchmark_cash_rem + sum(
                benchmark_shares.get(t, 0) * _last_close_prices.get(t, 0)
                for t in benchmark_shares
            )
            bm_return = bm_nav / initial_cash - 1
            alpha = total_return - bm_return
            print(f"{'─'*50}")
            print(f"  基准收益率: {bm_return:>+12.2%}  （买入持有不动）")
            print(f"  超额收益  : {alpha:>+12.2%}  {'✅ 跑赢基准' if alpha > 0 else '❌ 跑输基准'}")
        print(f"{'='*50}")

        print(f"  收益曲线  → {csv_path}（逐日追加，可分段续跑）")
        print(f"{'='*50}\n")
    else:
        print("  数据不足，无法计算绩效指标")
        print(f"{'='*50}\n")

    # ── 关闭长期记忆上下文 ──────────────────────────────────────────────────
    await _ltm_stack.__aexit__(None, None, None)
    # ──────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-02")
    parser.add_argument("--end",   default="2024-01-02")
    parser.add_argument("--config-name", default="local_test")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="忽略历史组合状态，从初始资金重新开始",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help="为所有 analyst agent 启用长期记忆（ReMeTaskLongTermMemory）",
    )
    parser.add_argument(
        "--tickers",
        default=None,
        help="逗号分隔的 ticker 列表；传入后优先于 .env 的 TICKERS",
    )
    args = parser.parse_args()
    tickers_override = (
        [item.strip() for item in args.tickers.split(",") if item.strip()]
        if args.tickers
        else None
    )

    asyncio.run(run(
        args.start, args.end, args.config_name,
        reset=args.reset,
        use_long_term_memory=args.memory,
        tickers_override=tickers_override,
    ))
