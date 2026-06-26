"""从真实回测结果(team_dashboard + leaderboard 信号)生成前端离线演示文件。

为什么不用 build_offline_feed.py：那个脚本需要 `{date}_reasoning.txt` 推理日志，
但 evo2025_q1 的推理日志(stdout)已被系统清理，只剩结构化的 leaderboard 信号。
本脚本直接用真实信号造 Activity Feed，保证 dashboard 与 feed 同源、股票一致。

用法：
    python scripts/build_demo_from_results.py --config evo2025_q1 --max-days 4
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

AGENT_ORDER = [
    "fundamentals_analyst", "technical_analyst", "sentiment_analyst",
    "valuation_analyst", "risk_manager", "portfolio_manager",
]
AGENT_NAMES = {
    "fundamentals_analyst": "基本面分析师",
    "technical_analyst": "技术面分析师",
    "sentiment_analyst": "情绪面分析师",
    "valuation_analyst": "估值分析师",
    "risk_manager": "风控经理",
    "portfolio_manager": "组合经理",
}
# 8 只股池 代码→名字
TICKER_NAMES = {
    "002668.SZ": "奥马电器(TCL智家)", "605499.SH": "东鹏饮料",
    "002847.SZ": "盐津铺子", "603338.SH": "浙江鼎力",
    "600377.SH": "宁沪高速", "603119.SH": "浙江荣泰",
    "600642.SH": "申能股份", "001328.SZ": "登康口腔",
}
SIGNAL_CN = {"bull": "看多", "bear": "看空", "neutral": "中性"}


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def date_to_base_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def summarize_agent_day(agent_id: str, day_signals: list[dict]) -> str:
    """把某 agent 当天对 8 只股的方向，summarize 成可读中文。"""
    buckets: dict[str, list[str]] = defaultdict(list)
    for s in day_signals:
        name = TICKER_NAMES.get(s["ticker"], s["ticker"])
        buckets[s["signal"]].append(name)
    parts = []
    for sig in ("bull", "bear", "neutral"):
        if buckets.get(sig):
            parts.append(f"{SIGNAL_CN[sig]}：{'、'.join(buckets[sig])}")
    label = AGENT_NAMES.get(agent_id, agent_id)
    if agent_id == "portfolio_manager":
        return f"【{label}综合决策】{'；'.join(parts)}" if parts else f"【{label}】今日观望"
    return f"{'；'.join(parts)}" if parts else "今日无明确方向"


def build_feed_events(leaderboard: list[dict], max_days: int) -> tuple[list[dict], list[str]]:
    # agent -> date -> [signals]
    by_agent_date: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    all_dates: set[str] = set()
    for entry in leaderboard:
        aid = entry.get("agentId")
        for s in entry.get("signals", []):
            by_agent_date[aid][s["date"]].append(s)
            all_dates.add(s["date"])

    days = sorted(all_dates)[-max_days:]
    events: list[dict] = []
    for di, date in enumerate(days):
        base = date_to_base_ms(date) + di * 60_000
        cid = f"offline-{date}"
        events.append({"type": "day_start", "date": date,
                       "content": f"离线回放 · {date}", "timestamp": base})
        events.append({"type": "conference_start", "conferenceId": cid,
                       "title": f"A股每日决策会议 · {date}",
                       "participants": AGENT_ORDER, "timestamp": base + 1000})
        for off, aid in enumerate(AGENT_ORDER, start=2):
            day_sig = by_agent_date.get(aid, {}).get(date, [])
            if not day_sig:
                continue
            events.append({
                "type": "conference_message", "conferenceId": cid,
                "agentId": aid, "agentName": AGENT_NAMES.get(aid, aid),
                "content": summarize_agent_day(aid, day_sig),
                "timestamp": base + off * 1000,
            })
        events.append({"type": "conference_end", "conferenceId": cid, "timestamp": base + 10_000})
        events.append({"type": "day_complete", "date": date,
                       "content": f"离线回放完成 · {date}", "timestamp": base + 11_000})
    # 正序：从最早一天到最晚一天，符合"回放/进展"直觉
    events.sort(key=lambda e: e["timestamp"])
    return events, days


def build_dashboard_payload(dashboard_dir: Path, leaderboard: list[dict]) -> dict:
    summary = read_json(dashboard_dir / "summary.json", {})
    stats = read_json(dashboard_dir / "stats.json", {})
    holdings = read_json(dashboard_dir / "holdings.json", [])
    trades = read_json(dashboard_dir / "trades.json", [])
    return {
        "source": str(dashboard_dir.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "total_value": summary.get("totalAssetValue") or summary.get("balance"),
            "pnl_percent": summary.get("pnlPct") or summary.get("totalReturn") or stats.get("totalReturn"),
            "equity": summary.get("equity", []),
            "baseline": summary.get("baseline", []),
            "baseline_vw": summary.get("baseline_vw", []),
            "momentum": summary.get("momentum", []),
            "strategies": summary.get("strategies", []),
        },
        "dashboard": {"stats": stats or summary, "holdings": holdings,
                      "trades": trades, "leaderboard": leaderboard},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="evo2025_q1")
    ap.add_argument("--max-days", type=int, default=4)
    ap.add_argument("--feed-output", type=Path, default=ROOT / "frontend" / "public" / "demo_feed.json")
    ap.add_argument("--dashboard-output", type=Path, default=ROOT / "frontend" / "public" / "demo_dashboard.json")
    args = ap.parse_args()

    dash_dir = ROOT / args.config / "team_dashboard"
    leaderboard = read_json(dash_dir / "leaderboard.json", [])
    if not leaderboard:
        raise SystemExit(f"找不到 leaderboard：{dash_dir/'leaderboard.json'}")

    events, days = build_feed_events(leaderboard, args.max_days)
    feed = {
        "source": f"{args.config}/team_dashboard",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events), "days": days,
        "leaderboard": leaderboard, "events": events,
    }
    args.feed_output.parent.mkdir(parents=True, exist_ok=True)
    args.feed_output.write_text(json.dumps(feed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ feed: {len(events)} events, {len(days)} days → {args.feed_output}")

    dashboard = build_dashboard_payload(dash_dir, leaderboard)
    args.dashboard_output.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ dashboard → {args.dashboard_output}")


if __name__ == "__main__":
    main()
