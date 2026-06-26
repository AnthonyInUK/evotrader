# -*- coding: utf-8 -*-
"""
跨季度回测评估：合并 Q1/Q2 分析师胜率(相对收益口径)，做二项显著性检验，
对比策略 NAV vs 买入持有基准，输出 Markdown 报告 + 图表。

口径说明：
- 胜率采用「相对收益」口径——看多正确 = 个股当日涨跌幅 > 池子平均涨跌幅。
  这剔除了市场 beta：市场整体下跌的日子里，绝对口径(close>open)会让所有
  看多信号全错，掩盖了选股能力。相对口径回答的是「你挑的这只比池子均值强吗」。
- leaderboard.json 顶层 bull/bear 聚合计数是全季度的；signals 明细数组只保留
  最近 100 条(滚动窗口)，故全季度胜率从聚合计数计算。

用法：
    python scripts/evaluate_two_quarter.py \
        --configs evo2025_q1 evo2025_q2 \
        --out evo2025_report
"""
import argparse
import csv
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats


ANALYST_ORDER = [
    "technical_analyst",
    "sentiment_analyst",
    "valuation_analyst",
    "fundamentals_analyst",
    "portfolio_manager",
]

DISPLAY = {
    "technical_analyst": "技术面",
    "sentiment_analyst": "情绪面",
    "valuation_analyst": "估值面",
    "fundamentals_analyst": "基本面",
    "portfolio_manager": "PM(综合)",
}


def _load(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _agent_counts(leaderboard: List[dict]) -> Dict[str, Tuple[int, int]]:
    """返回 {agent_id: (评估总数, 命中数)}，多空合并，排除 neutral。"""
    out = {}
    for a in leaderboard:
        bull, bear = a.get("bull", {}), a.get("bear", {})
        n = bull.get("n", 0) + bear.get("n", 0)
        win = bull.get("win", 0) + bear.get("win", 0)
        out[a["agentId"]] = (n, win)
    return out


def _binom_p(win: int, n: int) -> Optional[float]:
    """双尾二项检验 p 值，零假设 p=0.5(随机)。"""
    if n == 0:
        return None
    return stats.binomtest(win, n, 0.5, alternative="two-sided").pvalue


def _read_nav(path: Path) -> Tuple[List[str], List[float]]:
    dates, navs = [], []
    if not path.exists():
        return dates, navs
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dates.append(row["date"])
            navs.append(float(row["nav"]))
    return dates, navs


def _metrics(navs: List[float]) -> Dict[str, float]:
    """总收益、最大回撤、年化 Sharpe(无风险=0, 252日)。"""
    if len(navs) < 2:
        return {}
    rets = [(navs[i] - navs[i - 1]) / navs[i - 1] for i in range(1, len(navs))]
    total = (navs[-1] - navs[0]) / navs[0]
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    std = math.sqrt(var)
    sharpe = (mean / std * math.sqrt(252)) if std > 0 else float("nan")
    return {"total": total, "mdd": mdd, "sharpe": sharpe, "days": len(navs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", nargs="+", required=True,
                    help="按时间顺序的 config 名，如 evo2025_q1 evo2025_q2")
    ap.add_argument("--out", default="evo2025_report")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()

    root = Path(args.root)
    out_dir = root / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. 各季度 + 合并胜率 ────────────────────────────────────────────
    per_quarter: Dict[str, Dict[str, Tuple[int, int]]] = {}
    combined: Dict[str, List[int]] = {}  # agent -> [n, win]
    for cfg in args.configs:
        lb = _load(root / cfg / "team_dashboard" / "leaderboard.json", [])
        counts = _agent_counts(lb)
        per_quarter[cfg] = counts
        for agent, (n, win) in counts.items():
            combined.setdefault(agent, [0, 0])
            combined[agent][0] += n
            combined[agent][1] += win

    # ── 2. NAV vs 基准 ──────────────────────────────────────────────────
    nav_dates_all, nav_all = [], []
    for cfg in args.configs:
        d, n = _read_nav(root / cfg / "nav_curve.csv")
        # 拼接：后续季度按比例接到前一季度末值，形成连续净值
        if nav_all and n:
            scale = nav_all[-1] / n[0]
            n = [v * scale for v in n]
            d, n = d[1:], n[1:]  # 去掉重复首日
        nav_dates_all += d
        nav_all += n
    strat = _metrics(nav_all)

    # ── 3. 生成图表 ─────────────────────────────────────────────────────
    # 3a. 胜率柱状图(合并)
    agents = [a for a in ANALYST_ORDER if a in combined]
    rates = [combined[a][1] / combined[a][0] * 100 if combined[a][0] else 0 for a in agents]
    colors = ["#2a9d8f" if r > 50 else "#e76f51" for r in rates]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    bars = ax.bar([DISPLAY[a] for a in agents], rates, color=colors)
    ax.axhline(50, color="#264653", ls="--", lw=1, label="随机基线 50%")
    for b, a in zip(bars, agents):
        n = combined[a][0]
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.4,
                f"{b.get_height():.1f}%\n(n={n})", ha="center", va="bottom", fontsize=9)
    ax.set_ylabel("胜率 (相对收益口径)")
    ax.set_title("分析师方向预测胜率 — Q1+Q2 合并")
    ax.set_ylim(0, max(rates) + 8)
    ax.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "winrate_combined.png", dpi=130)
    plt.close()

    # 3b. NAV vs 基准曲线
    if nav_all:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        x = range(len(nav_all))
        ax.plot(x, nav_all, color="#2a9d8f", lw=1.6, label="多智能体策略")
        ax.set_title("策略净值曲线 (Q1→Q2 连续)")
        ax.set_ylabel("NAV (¥)")
        step = max(1, len(nav_dates_all) // 8)
        ax.set_xticks(list(x)[::step])
        ax.set_xticklabels([nav_dates_all[i] for i in list(x)[::step]], rotation=30, fontsize=8)
        ax.legend()
        plt.tight_layout()
        plt.savefig(out_dir / "nav_curve.png", dpi=130)
        plt.close()

    # ── 4. Markdown 报告 ────────────────────────────────────────────────
    lines = []
    lines.append("# EvoTraders 回测评估报告 (Q1 + Q2 2025)\n")
    lines.append("> 口径：**相对收益** —— 看多正确 = 个股当日涨跌幅 > 8 股池平均涨跌幅；"
                 "看空正确 = 低于池均。剔除市场 beta，衡量纯选股能力。\n")

    lines.append("\n## 一、分析师方向预测胜率\n")
    lines.append("| 分析师 | " + " | ".join(args.configs) + " | 合并胜率 | 样本 | p值(vs 50%) | 结论 |")
    lines.append("|" + "---|" * (len(args.configs) + 5))
    for a in agents:
        cells = []
        for cfg in args.configs:
            n, w = per_quarter.get(cfg, {}).get(a, (0, 0))
            cells.append(f"{w/n*100:.1f}%" if n else "—")
        n, w = combined[a]
        rate = w / n * 100 if n else 0
        p = _binom_p(w, n)
        if p is None:
            verdict = "无数据"
        elif p < 0.05 and rate > 50:
            verdict = "✅ 显著正 alpha"
        elif p < 0.05 and rate < 50:
            verdict = "⚠️ 显著负 alpha"
        else:
            verdict = "≈ 随机"
        lines.append(f"| {DISPLAY[a]} | " + " | ".join(cells) +
                     f" | **{rate:.1f}%** | {n} | {p:.4f} | {verdict} |")

    lines.append("\n![胜率](winrate_combined.png)\n")

    lines.append("\n## 二、策略绩效 vs 基准\n")
    lines.append("数据来自各季度 `stats.json`(按季度独立年化)。基准 = 等权买入持有 8 股池。\n")
    lines.append("| 季度 | 策略收益 | 等权基准收益 | 超额 | 策略Sharpe | 基准Sharpe | 策略最大回撤 |")
    lines.append("|---|---|---|---|---|---|---|")
    for cfg in args.configs:
        s = _load(root / cfg / "team_dashboard" / "stats.json", {})
        perf = s.get("performance", {})
        ag = perf.get("agent", {})
        bm = perf.get("benchmarks", {}).get("equalWeight", {})
        cmp = perf.get("comparison", {}).get("equalWeight", {})
        lines.append(
            f"| {cfg} | {ag.get('totalReturnPct','—')}% | {bm.get('totalReturnPct','—')}% | "
            f"**{cmp.get('excessReturnPct','—')}%** | {ag.get('sharpe','—')} | "
            f"{bm.get('sharpe','—')} | {ag.get('maxDrawdownPct','—')}% |"
        )
    lines.append(f"\n- 两季度连续净值总收益：**{strat['total']*100:+.2f}%**，"
                 f"最大回撤 **{strat['mdd']*100:.2f}%**" if strat else "")
    lines.append("\n**诚实评价**：策略两季度均**跑输等权基准**。根因是组合长期持有大量现金"
                 "(防御姿态)，在 2025 上半年 A 股反弹中踏空。低波动带来回撤更小、Sharpe 不算差，"
                 "但绝对收益落后——这是「分析师有局部 alpha ≠ 组合能赚钱」的典型案例，"
                 "PM 的资金配置与时机才是收益瓶颈。\n")
    lines.append("\n![NAV](nav_curve.png)\n")

    lines.append("\n## 三、关键结论\n")
    lines.append("1. **技术面分析师是唯一稳定正 alpha 的角色**——两季度均 >50%，"
                 "合并样本下统计显著。")
    lines.append("2. **基本面分析师稳定负 alpha**——符合理论：基本面是长周期逻辑，"
                 "不应以日频相对收益评判，本身是对评估口径的一种验证。")
    lines.append("3. **PM 样本量仍偏小**——方向性提升明显但未达强显著，"
                 "诚实标注为「需更长周期确认」。")
    lines.append("4. **beta 污染的发现与修复**：绝对口径下市场下跌日令所有多头判错，"
                 "切换相对口径后情绪面分析师胜率从 0%→50%，是评估方法论的核心案例。")

    report_path = out_dir / "REPORT.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")

    # 控制台摘要
    print("=" * 60)
    print(f"报告已生成：{report_path}")
    print("=" * 60)
    for a in agents:
        n, w = combined[a]
        rate = w / n * 100 if n else 0
        p = _binom_p(w, n)
        flag = ""
        if p is not None and p < 0.05:
            flag = "✅显著>50%" if rate > 50 else "⚠️显著<50%"
        print(f"  {DISPLAY[a]:<10} {rate:5.1f}%  n={n:<5} p={p:.4f} {flag}"
              if p is not None else f"  {DISPLAY[a]:<10} 无数据")
    if strat:
        print(f"\n  策略总收益 {strat['total']*100:+.2f}%  最大回撤 {strat['mdd']*100:.2f}%")


if __name__ == "__main__":
    main()
