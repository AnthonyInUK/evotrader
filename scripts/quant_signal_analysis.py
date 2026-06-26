# -*- coding: utf-8 -*-
"""
量化口径的信号质量分析（IC/ICIR + 多持有期衰减 + 扣成本胜率 + 朴素基线对比）。

为什么需要：胜率(方向对错)是粗口径；量化研究更看信号强度与未来收益的相关性(IC)、
alpha 衰减速度(多持有期)、扣交易成本后是否仍有效、以及是否真比一条简单规则强。

数据来源：
- 信号(含 confidence)从运行日志解析：`<analyst>_analyst:` 块后跟
  `<TICKER>: UP|DOWN|NEUTRAL (confidence: X%)`，配合 `📅 日期` 标记。
  (confidence 仅在内存态，未持久化，故只能从日志回捞；脚本报告覆盖率。)
- 价格从 backend/data/akshare_cache/<code>_<EX>_*.parquet (OHLC 日线)。

口径：
- 信号强度 = confidence × 方向符号 (UP=+1, DOWN=-1, NEUTRAL=0)，范围 [-1, 1]。
- IC = 每日横截面上「信号强度 vs 未来收益」的 Pearson 相关，再按日平均。
- 前瞻收益：T+0 = 当日 open→close；T+k = 信号日 close → 第 k 日 close。
- 交易成本：A股双边 ≈ 印花税0.05%(卖) + 佣金0.025%×2 + 滑点0.1%×2 ≈ 0.35% 单次往返。

用法：
    python scripts/quant_signal_analysis.py \
        --logs /tmp/evo_q1_final.log /tmp/evo_q1_mar.log /tmp/evo_q1_feb.log \
        --out evo2025_report
"""
import argparse
import glob
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROUND_TRIP_COST = 0.0035  # 单次往返交易成本
PRICE_DIR = Path("backend/data/akshare_cache")

_DATE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")
_NAME_RE = re.compile(r"^(\w+_analyst):")
_PRED_RE = re.compile(
    r"^(\d{6}\.(?:SZ|SH)):\s*(UP|DOWN|NEUTRAL)\s*\(confidence:\s*(\d+)%\)"
)
_DIR_SIGN = {"UP": 1, "DOWN": -1, "NEUTRAL": 0}


def parse_signals(log_paths: List[str]) -> Dict[Tuple[str, str, str], Tuple[int, float]]:
    """解析 (date, analyst, ticker) -> (sign, strength) ；同键后者覆盖前者(取最终块)。"""
    recs: Dict[Tuple[str, str, str], Tuple[int, float]] = {}
    for path in log_paths:
        cur_date = cur_analyst = None
        pending = 0
        try:
            f = open(path, errors="ignore")
        except FileNotFoundError:
            continue
        for line in f:
            s = line.strip()
            d = _DATE_RE.search(line)
            if d:
                cur_date = d.group(1)
            m = _NAME_RE.match(s)
            if m:
                cur_analyst = m.group(1)
                pending = 8
                continue
            p = _PRED_RE.match(s)
            if p and cur_date and cur_analyst and pending > 0:
                sign = _DIR_SIGN[p.group(2)]
                strength = sign * int(p.group(3)) / 100.0
                recs[(cur_date, cur_analyst, p.group(1))] = (sign, strength)
                pending -= 1
        f.close()
    return recs


def load_prices() -> Dict[str, pd.DataFrame]:
    """ticker(002668.SZ) -> DataFrame[index=date str, open/close]。"""
    out: Dict[str, pd.DataFrame] = {}
    for fp in glob.glob(str(PRICE_DIR / "*.parquet")):
        name = Path(fp).name  # 002668_SZ_2023-..-2026-...parquet
        code_ex = "_".join(name.split("_")[:2])  # 002668_SZ
        ticker = code_ex.replace("_", ".")        # 002668.SZ
        df = pd.read_parquet(fp)
        df = df.copy()
        df["d"] = pd.to_datetime(df["time"]).dt.strftime("%Y-%m-%d")
        out[ticker] = df.reset_index(drop=True)
    return out


def forward_returns(
    prices: Dict[str, pd.DataFrame], ticker: str, date: str, horizons=(0, 1, 3, 5)
) -> Dict[int, Optional[float]]:
    """T+0=open→close；T+k=close→第k日close。无数据返回 None。"""
    df = prices.get(ticker)
    out = {h: None for h in horizons}
    if df is None:
        return out
    idx = df.index[df["d"] == date]
    if len(idx) == 0:
        return out
    i = idx[0]
    for h in horizons:
        if h == 0:
            o, c = df.at[i, "open"], df.at[i, "close"]
            if o > 0:
                out[0] = (c - o) / o
        else:
            j = i + h
            if j < len(df):
                c0, ck = df.at[i, "close"], df.at[j, "close"]
                if c0 > 0:
                    out[h] = (ck - c0) / c0
    return out


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def analyze():
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", nargs="+", required=True)
    ap.add_argument("--out", default="evo2025_report")
    ap.add_argument("--horizons", nargs="+", type=int, default=[0, 1, 3, 5])
    args = ap.parse_args()

    horizons = tuple(args.horizons)
    recs = parse_signals(args.logs)
    prices = load_prices()

    dates = sorted(set(k[0] for k in recs))
    analysts = sorted(set(k[1] for k in recs))
    print(f"解析信号: {len(recs)} 条 | {len(dates)} 天 ({dates[0]}→{dates[-1]}) | {len(analysts)} 分析师\n")

    lines = ["# 量化口径信号质量分析\n"]
    lines.append(f"> 样本：{len(recs)} 条信号，{len(dates)} 交易日 "
                 f"({dates[0]} → {dates[-1]})，{len(analysts)} 分析师。"
                 f"信号强度 = confidence×方向；前瞻收益 close→close。\n")

    # ── 预计算每条信号的前瞻收益 ────────────────────────────────────────
    # rows: (date, analyst, ticker, sign, strength, {h: fwd_ret})
    rows = []
    for (date, analyst, ticker), (sign, strength) in recs.items():
        fwd = forward_returns(prices, ticker, date, horizons)
        rows.append((date, analyst, ticker, sign, strength, fwd))

    # ── 1. IC / ICIR (按分析师，T+1 横截面) ─────────────────────────────
    lines.append("\n## 1. IC / ICIR（信号强度 vs 未来收益的相关性）\n")
    lines.append("| 分析师 | IC均值(T+1) | IC标准差 | ICIR | IC>0占比 |")
    lines.append("|---|---|---|---|---|")
    ic_horizon = 1 if 1 in horizons else horizons[0]
    for analyst in analysts:
        daily_ic = []
        for date in dates:
            xs, ys = [], []
            for (d, a, t, sign, strength, fwd) in rows:
                if d == date and a == analyst and fwd.get(ic_horizon) is not None:
                    xs.append(strength)
                    ys.append(fwd[ic_horizon])
            if len(xs) >= 3 and len(set(xs)) > 1:
                ic = pd.Series(xs).corr(pd.Series(ys))
                if pd.notna(ic):
                    daily_ic.append(ic)
        if daily_ic:
            s = pd.Series(daily_ic)
            icir = s.mean() / s.std() if s.std() > 0 else float("nan")
            pos = (s > 0).mean()
            lines.append(f"| {analyst} | {s.mean():+.4f} | {s.std():.4f} | "
                         f"{icir:+.3f} | {pos:.0%} |")
    lines.append("\n> ICIR>0.3 通常视为有效信号；A股日频 IC 均值 0.03~0.05 即可用。")

    # ── 2. 多持有期衰减 (方向胜率) ──────────────────────────────────────
    lines.append("\n## 2. 多持有期衰减（方向命中率，剔除中性）\n")
    header = "| 分析师 | " + " | ".join(f"T+{h}" for h in horizons) + " |"
    lines.append(header)
    lines.append("|" + "---|" * (len(horizons) + 1))
    for analyst in analysts:
        cells = []
        for h in horizons:
            hit = tot = 0
            for (d, a, t, sign, strength, fwd) in rows:
                if a == analyst and sign != 0 and fwd.get(h) is not None:
                    tot += 1
                    if (sign > 0 and fwd[h] > 0) or (sign < 0 and fwd[h] < 0):
                        hit += 1
            cells.append(f"{hit/tot:.1%}" if tot else "—")
        lines.append(f"| {analyst} | " + " | ".join(cells) + " |")
    lines.append("\n> 命中率随 T 增大若快速回落=alpha 衰减快(适合短持有)；若稳定=趋势型。")

    # ── 3. 扣交易成本 (T+1 净收益胜率) ─────────────────────────────────
    lines.append(f"\n## 3. 扣交易成本后（往返成本 {ROUND_TRIP_COST:.2%}，T+1）\n")
    lines.append("| 分析师 | 毛胜率 | 净胜率(扣成本) | 平均净收益/笔 |")
    lines.append("|---|---|---|---|")
    for analyst in analysts:
        gross_hit = net_hit = tot = 0
        net_sum = 0.0
        for (d, a, t, sign, strength, fwd) in rows:
            r = fwd.get(ic_horizon)
            if a == analyst and sign != 0 and r is not None:
                tot += 1
                directional = sign * r            # 跟对方向的毛收益
                net = directional - ROUND_TRIP_COST
                if directional > 0:
                    gross_hit += 1
                if net > 0:
                    net_hit += 1
                net_sum += net
        if tot:
            lines.append(f"| {analyst} | {gross_hit/tot:.1%} | {net_hit/tot:.1%} | "
                         f"{net_sum/tot:+.3%} |")
    lines.append("\n> 扣成本后净胜率/净收益转负=信号无法覆盖摩擦成本，不可落地。")

    # ── 4. 朴素 RSI 基线对比 (T+1) ──────────────────────────────────────
    lines.append("\n## 4. 朴素基线对比（RSI(14)<30 做多 / >70 做空，T+1）\n")
    base_hit = base_tot = 0
    for ticker, df in prices.items():
        if ticker not in set(t for (_, _, t, *_ ) in rows):
            continue
        df = df.copy()
        df["rsi"] = rsi(df["close"])
        date_set = set(d for (d, *_ ) in rows)
        for i in range(len(df) - max(horizons)):
            if df.at[i, "d"] not in date_set:
                continue
            rv = df.at[i, "rsi"]
            if pd.isna(rv):
                continue
            sign = 1 if rv < 30 else (-1 if rv > 70 else 0)
            if sign == 0:
                continue
            c0, ck = df.at[i, "close"], df.at[i + ic_horizon, "close"]
            if c0 > 0:
                base_tot += 1
                if (sign > 0 and ck > c0) or (sign < 0 and ck < c0):
                    base_hit += 1
    base_rate = base_hit / base_tot if base_tot else float("nan")
    # 全体分析师 T+1 平均胜率
    all_hit = all_tot = 0
    for (d, a, t, sign, strength, fwd) in rows:
        r = fwd.get(ic_horizon)
        if sign != 0 and r is not None:
            all_tot += 1
            if (sign > 0 and r > 0) or (sign < 0 and r < 0):
                all_hit += 1
    llm_rate = all_hit / all_tot if all_tot else float("nan")
    lines.append(f"- RSI 基线胜率(T+1)：**{base_rate:.1%}** (n={base_tot})")
    lines.append(f"- LLM 分析师平均胜率(T+1)：**{llm_rate:.1%}** (n={all_tot})")
    lines.append(f"- 超额：**{(llm_rate - base_rate)*100:+.1f}pp** "
                 f"{'✅ LLM 优于朴素规则' if llm_rate > base_rate else '⚠️ 未跑赢朴素规则'}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / "QUANT_ANALYSIS.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    print("\n".join(lines))
    print(f"\n报告已写入：{report}")


if __name__ == "__main__":
    analyze()
