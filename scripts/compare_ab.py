# -*- coding: utf-8 -*-
"""
记忆 A/B 对比：有记忆(A) vs 无记忆(B)，同口径、同时段，从运行日志解析信号。

为什么走日志而非 leaderboard：A 组经历多次崩溃重启，leaderboard 聚合计数被部分
重置损坏(技术面显示 n=120，实际日志有 432)。日志是逐条追加、未被重置的可靠源。

对比指标(限定 A/B 重叠日期)：
- T+0 相对胜率(剔除beta)：个股当日涨跌 vs 池均
- T+1 IC：信号强度 vs 次日收益的横截面相关
- T+1 方向命中率
- 扣成本后净收益/笔

用法：
    python scripts/compare_ab.py \
        --a-logs /tmp/evo_q1_mem.log /tmp/evo2025_q1_mem_auto.log \
        --b-logs /tmp/evo_q1_final.log /tmp/evo_q1_mar.log /tmp/evo_q1_feb.log \
        --out evo2025_report
"""
import argparse
from pathlib import Path

import pandas as pd

from quant_signal_analysis import (  # 复用已有解析/价格/收益逻辑
    ROUND_TRIP_COST,
    forward_returns,
    load_prices,
    parse_signals,
)

ANALYSTS = ["technical_analyst", "sentiment_analyst",
            "valuation_analyst", "fundamentals_analyst"]


def _rows(recs, prices, dates_keep):
    rows = []
    for (date, analyst, ticker), (sign, strength) in recs.items():
        if date not in dates_keep:
            continue
        fwd = forward_returns(prices, ticker, date, (0, 1))
        rows.append((date, analyst, ticker, sign, strength, fwd))
    return rows


def _pool_avg_t0(prices, date, tickers):
    rets = []
    for t in tickers:
        fr = forward_returns(prices, t, date, (0,)).get(0)
        if fr is not None:
            rets.append(fr)
    return sum(rets) / len(rets) if rets else None


def _metrics(rows, prices):
    """返回 {analyst: dict(rel_wr, ic, hit_t1, net_t1, n)}。"""
    out = {}
    # 预备每日池均(T+0)
    dates = sorted(set(r[0] for r in rows))
    pool_tickers = sorted(set(r[2] for r in rows))
    pool_avg = {d: _pool_avg_t0(prices, d, pool_tickers) for d in dates}

    for a in ANALYSTS:
        ar = [r for r in rows if r[1] == a]
        # 相对胜率 T+0
        rel_hit = rel_tot = 0
        for (d, _, t, sign, st, fwd) in ar:
            r0, pa = fwd.get(0), pool_avg.get(d)
            if sign != 0 and r0 is not None and pa is not None:
                rel = r0 - pa
                rel_tot += 1
                if (sign > 0 and rel > 0) or (sign < 0 and rel < 0):
                    rel_hit += 1
        # IC T+1 (按日横截面)
        daily_ic = []
        for d in dates:
            xs = [st for (dd, _, t, sign, st, fwd) in ar if dd == d and fwd.get(1) is not None]
            ys = [fwd[1] for (dd, _, t, sign, st, fwd) in ar if dd == d and fwd.get(1) is not None]
            if len(xs) >= 3 and len(set(xs)) > 1:
                ic = pd.Series(xs).corr(pd.Series(ys))
                if pd.notna(ic):
                    daily_ic.append(ic)
        ic_mean = sum(daily_ic) / len(daily_ic) if daily_ic else float("nan")
        # T+1 命中 + 扣成本净收益
        hit = tot = 0
        net_sum = 0.0
        for (d, _, t, sign, st, fwd) in ar:
            r1 = fwd.get(1)
            if sign != 0 and r1 is not None:
                tot += 1
                if (sign > 0 and r1 > 0) or (sign < 0 and r1 < 0):
                    hit += 1
                net_sum += sign * r1 - ROUND_TRIP_COST
        out[a] = {
            "rel_wr": rel_hit / rel_tot if rel_tot else float("nan"),
            "ic": ic_mean,
            "hit_t1": hit / tot if tot else float("nan"),
            "net_t1": net_sum / tot if tot else float("nan"),
            "n": tot,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a-logs", nargs="+", required=True)
    ap.add_argument("--b-logs", nargs="+", required=True)
    ap.add_argument("--out", default="evo2025_report")
    args = ap.parse_args()

    a_recs = parse_signals(args.a_logs)
    b_recs = parse_signals(args.b_logs)
    prices = load_prices()

    a_dates = set(k[0] for k in a_recs)
    b_dates = set(k[0] for k in b_recs)
    overlap = a_dates & b_dates
    print(f"A 组日期 {len(a_dates)}，B 组日期 {len(b_dates)}，重叠 {len(overlap)} 天")
    print(f"重叠区间: {min(overlap)} → {max(overlap)}\n")

    a_rows = _rows(a_recs, prices, overlap)
    b_rows = _rows(b_recs, prices, overlap)
    A = _metrics(a_rows, prices)
    B = _metrics(b_rows, prices)

    lines = ["# 记忆 A/B 对比（同时段同口径）\n"]
    lines.append(f"> 重叠区间 {min(overlap)} → {max(overlap)}（{len(overlap)} 交易日）；"
                 f"两组均从运行日志解析(规避 A 组 leaderboard 重启损坏)。\n")
    lines.append("\n| 分析师 | 指标 | B(无记忆) | A(有记忆) | Δ(A−B) |")
    lines.append("|---|---|---|---|---|")

    def fmt(v, pct=True):
        if v != v:  # nan
            return "—"
        return f"{v*100:.1f}%" if pct else f"{v:+.4f}"

    for a in ANALYSTS:
        b, aa = B[a], A[a]
        name = a.replace("_analyst", "")
        lines.append(f"| {name} | 相对胜率T0 | {fmt(b['rel_wr'])} | {fmt(aa['rel_wr'])} | "
                     f"{(aa['rel_wr']-b['rel_wr'])*100:+.1f}pp |")
        lines.append(f"| | IC(T+1) | {fmt(b['ic'],False)} | {fmt(aa['ic'],False)} | "
                     f"{(aa['ic']-b['ic']):+.4f} |")
        lines.append(f"| | 命中T+1 | {fmt(b['hit_t1'])} | {fmt(aa['hit_t1'])} | "
                     f"{(aa['hit_t1']-b['hit_t1'])*100:+.1f}pp |")
        lines.append(f"| | 净收益/笔T+1 | {fmt(b['net_t1'])} | {fmt(aa['net_t1'])} | "
                     f"{(aa['net_t1']-b['net_t1'])*100:+.2f}pp |")

    # 汇总：全体平均相对胜率
    def avg(grp, key):
        vs = [grp[a][key] for a in ANALYSTS if grp[a][key] == grp[a][key]]
        return sum(vs) / len(vs) if vs else float("nan")

    lines.append("\n**汇总（4 分析师均值）**")
    lines.append(f"- 相对胜率 T0：B {avg(B,'rel_wr')*100:.1f}% → A {avg(A,'rel_wr')*100:.1f}% "
                 f"({(avg(A,'rel_wr')-avg(B,'rel_wr'))*100:+.1f}pp)")
    lines.append(f"- IC T+1：B {avg(B,'ic'):+.4f} → A {avg(A,'ic'):+.4f} "
                 f"({avg(A,'ic')-avg(B,'ic'):+.4f})")
    lines.append(f"- 扣成本净收益/笔：B {avg(B,'net_t1')*100:+.2f}% → A {avg(A,'net_t1')*100:+.2f}%")

    out = Path(args.out) / "AB_COMPARISON.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n报告写入：{out}")


if __name__ == "__main__":
    main()
