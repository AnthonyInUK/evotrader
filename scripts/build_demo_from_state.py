"""从回测产生的 _internal_state.json 生成带完整 Phase 结构的 demo_feed.json。

与 build_demo_from_results.py 的区别：
  - 那个脚本只有信号摘要（bull/bear/neutral）
  - 这个脚本用回测时保存的完整 LLM 推理文字，agent 发言有血有肉

用法：
    python scripts/build_demo_from_state.py --config evo2025_q1
    python scripts/build_demo_from_state.py --config evo2025_q1 --max-days 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# DeepSeek/AgentScope 工具调用标记（全角竖线 ｜｜），不该展示给用户
_DSML_TOOLCALL_RE = re.compile(r"<｜+DSML｜+tool_calls>.*", re.DOTALL)
_DSML_TAG_RE = re.compile(r"</?｜+DSML｜+[^>]*>")


def clean_content(text: str) -> str:
    """去掉 ReAct 工具调用的底层 DSML 标记，只保留 agent 的自然语言推理。"""
    if not text:
        return ""
    # tool_calls 块及其之后全是机器标记，整段截掉
    text = _DSML_TOOLCALL_RE.sub("", text)
    # 清理任何残留的单个 DSML 标签
    text = _DSML_TAG_RE.sub("", text)
    return text.strip()

ROOT = Path(__file__).resolve().parents[1]

AGENT_DISPLAY = {
    "fundamentals_analyst": "Fundamentals Analyst",
    "technical_analyst":    "Technical Analyst",
    "sentiment_analyst":    "Sentiment Analyst",
    "valuation_analyst":    "Valuation Analyst",
    "risk_manager":         "Risk Manager",
    "portfolio_manager":    "Portfolio Manager",
}

# Phase 定义：每天的 6 个阶段
PHASES = [
    {"id": "p0",  "label": "Memory Clear",        "desc": "Clear short-term memory to prevent cross-day context pollution"},
    {"id": "p1a", "label": "Analyst Analysis",    "desc": "4 analysts run concurrently to analyze each stock"},
    {"id": "p1b", "label": "Risk Assessment",     "desc": "Risk Manager evaluates portfolio exposure and market risk"},
    {"id": "p2a", "label": "Conference Discussion","desc": "PM + analysts debate, weighted voting detects consensus"},
    {"id": "p3",  "label": "PM Decision",         "desc": "Portfolio Manager makes final buy/sell/hold decisions"},
    {"id": "p5",  "label": "Settlement & Memory", "desc": "Daily P&L calculated, agents write to long-term memory"},
]

ANALYST_IDS = {"fundamentals_analyst", "technical_analyst", "sentiment_analyst", "valuation_analyst"}


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def date_to_base_ms(date_str: str, offset_minutes: int = 0) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000) + offset_minutes * 60_000


def truncate(text: str, max_chars: int = 2000) -> str:
    text = clean_content(text)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n\n… [truncated, {len(text) - max_chars} chars remaining]"


# ── 中文重点总结（可选，--summarize 开启）──────────────────────────────────────
_SUMM_ENABLED = False
_SUMM_CACHE: dict[str, str] = {}
_SUMM_CACHE_PATH: Path | None = None
_SUMM_CLIENT = None
_SUMM_MODEL = "deepseek-chat"


def _load_summary_cache(config: str) -> None:
    global _SUMM_CACHE, _SUMM_CACHE_PATH
    _SUMM_CACHE_PATH = ROOT / config / "state" / "demo_summaries.json"
    _SUMM_CACHE = read_json(_SUMM_CACHE_PATH, {}) if _SUMM_CACHE_PATH.exists() else {}


def _save_summary_cache() -> None:
    if _SUMM_CACHE_PATH is not None:
        _SUMM_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SUMM_CACHE_PATH.write_text(
            json.dumps(_SUMM_CACHE, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _get_client():
    global _SUMM_CLIENT
    if _SUMM_CLIENT is None:
        from openai import OpenAI  # 延迟导入，未开启总结时无依赖
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise SystemExit("❌ --summarize 需要 DEEPSEEK_API_KEY（检查 .env）")
        _SUMM_CLIENT = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
    return _SUMM_CLIENT


def summarize_zh(text: str, role: str, max_sentences: int = 2) -> str:
    """把一条 agent 发言提炼成一两句简体中文重点；带缓存、失败降级到截断。"""
    cleaned = clean_content(text)
    if not cleaned:
        return ""
    key = hashlib.sha256(f"{role}||{cleaned}".encode("utf-8")).hexdigest()
    if key in _SUMM_CACHE:
        return _SUMM_CACHE[key]

    prompt = (
        f"下面是一位「{role}」在某个交易日的发言（可能混有英文、工具调用过程等噪音）。\n"
        f"请用**{max_sentences}句以内的简体中文**提炼他的核心观点：看多/看空了哪些股票、"
        f"最关键的理由是什么。只输出摘要本身，不要任何前缀、不要解释。\n\n"
        f"发言原文：\n{cleaned[:4000]}"
    )
    try:
        resp = _get_client().chat.completions.create(
            model=_SUMM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
            timeout=60,
        )
        summary = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # 网络/欠费等：降级，不让整个 build 失败
        print(f"   ⚠️ 总结失败（降级到截断）: {e}")
        return truncate(cleaned, 600)

    if not summary:
        return truncate(cleaned, 600)
    _SUMM_CACHE[key] = summary
    return summary


def display_content(text: str, role: str, max_chars: int = 2000) -> str:
    """展示用内容：开启 --summarize 时返回中文重点，否则清洗+截断。"""
    if _SUMM_ENABLED:
        return summarize_zh(text, role)
    return truncate(text, max_chars)


def build_events_for_day(
    date: str,
    day_idx: int,
    agent_messages: dict[str, str],       # agentId -> content (from agent_message events)
    conference_messages: list[dict],       # list of {agentId, content}
    trades: list[dict],
    pnl_summary: str,
) -> list[dict]:
    """把一天的所有数据组装成带 Phase 结构的事件列表。"""
    base = date_to_base_ms(date, offset_minutes=day_idx * 2)
    events = []
    t = base  # 滚动时间戳

    def push(evt: dict, delta_ms: int = 1000):
        nonlocal t
        evt["timestamp"] = t
        events.append(evt)
        t += delta_ms

    # ── day_start ──────────────────────────────────────────────────────────────
    push({"type": "day_start", "date": date, "content": f"Trading Day · {date}"})

    # ── Phase 0: Memory Clear ──────────────────────────────────────────────────
    push({"type": "phase_start", "phaseId": "p0", "date": date,
          "label": PHASES[0]["label"], "desc": PHASES[0]["desc"]})
    push({"type": "phase_end",   "phaseId": "p0", "date": date}, delta_ms=500)

    # ── Phase 1a: Analyst Analysis ─────────────────────────────────────────────
    push({"type": "phase_start", "phaseId": "p1a", "date": date,
          "label": PHASES[1]["label"], "desc": PHASES[1]["desc"]})
    for aid in ["fundamentals_analyst", "technical_analyst", "sentiment_analyst", "valuation_analyst"]:
        content = agent_messages.get(aid)
        if content:
            push({
                "type": "agent_message",
                "agentId": aid,
                "agentName": AGENT_DISPLAY.get(aid, aid),
                "content": display_content(content, AGENT_DISPLAY.get(aid, aid)),
                "phase": "p1a",
            }, delta_ms=2000)
    push({"type": "phase_end", "phaseId": "p1a", "date": date}, delta_ms=500)

    # ── Phase 1b: Risk Assessment ─────────────────────────────────────────────
    push({"type": "phase_start", "phaseId": "p1b", "date": date,
          "label": PHASES[2]["label"], "desc": PHASES[2]["desc"]})
    risk_content = agent_messages.get("risk_manager")
    if risk_content:
        push({
            "type": "agent_message",
            "agentId": "risk_manager",
            "agentName": AGENT_DISPLAY["risk_manager"],
            "content": display_content(risk_content, AGENT_DISPLAY["risk_manager"]),
            "phase": "p1b",
        }, delta_ms=2000)
    push({"type": "phase_end", "phaseId": "p1b", "date": date}, delta_ms=500)

    # ── Phase 2a: Conference Discussion ───────────────────────────────────────
    push({"type": "phase_start", "phaseId": "p2a", "date": date,
          "label": PHASES[3]["label"], "desc": PHASES[3]["desc"]})
    conf_id = f"conf-{date}"
    push({"type": "conference_start", "conferenceId": conf_id,
          "title": f"Investment Discussion · {date}",
          "participants": list(AGENT_DISPLAY.keys())})
    for msg in conference_messages:
        aid = msg.get("agentId", "")
        push({
            "type": "conference_message",
            "conferenceId": conf_id,
            "agentId": aid,
            "agentName": AGENT_DISPLAY.get(aid, msg.get("agentName", aid)),
            "content": display_content(msg.get("content", ""),
                                       AGENT_DISPLAY.get(aid, msg.get("agentName", aid)), 1500),
            "phase": "p2a",
        }, delta_ms=2000)
    push({"type": "conference_end", "conferenceId": conf_id}, delta_ms=500)
    push({"type": "phase_end", "phaseId": "p2a", "date": date}, delta_ms=500)

    # ── Phase 3: PM Decision ──────────────────────────────────────────────────
    push({"type": "phase_start", "phaseId": "p3", "date": date,
          "label": PHASES[4]["label"], "desc": PHASES[4]["desc"]})
    pm_content = agent_messages.get("portfolio_manager")
    if pm_content:
        push({
            "type": "agent_message",
            "agentId": "portfolio_manager",
            "agentName": AGENT_DISPLAY["portfolio_manager"],
            "content": display_content(pm_content, AGENT_DISPLAY["portfolio_manager"]),
            "phase": "p3",
        }, delta_ms=2000)
    # Trade execution events
    for trade in trades:
        action = trade.get("action", "hold").upper()
        ticker = trade.get("ticker", "")
        qty = trade.get("quantity", 0)
        price = trade.get("price", 0)
        push({
            "type": "trade_executed",
            "date": date,
            "ticker": ticker,
            "action": action,
            "quantity": qty,
            "price": price,
            "content": f"{action} {qty} shares of {ticker} @ ¥{price:.2f}",
            "phase": "p3",
        }, delta_ms=800)
    push({"type": "phase_end", "phaseId": "p3", "date": date}, delta_ms=500)

    # ── Phase 5: Settlement ────────────────────────────────────────────────────
    push({"type": "phase_start", "phaseId": "p5", "date": date,
          "label": PHASES[5]["label"], "desc": PHASES[5]["desc"]})
    if pnl_summary:
        push({
            "type": "settlement",
            "date": date,
            "content": pnl_summary,
            "phase": "p5",
        }, delta_ms=1000)
    push({"type": "phase_end", "phaseId": "p5", "date": date}, delta_ms=500)

    push({"type": "day_complete", "date": date,
          "content": f"Day complete · {date}"})

    return events


def group_by_date(feed_history: list[dict]) -> dict[str, dict]:
    """把 feed_history 按日期分组，提取每天的 agent 消息和会议消息。"""
    by_date: dict[str, dict] = defaultdict(lambda: {
        "agent_messages": {},
        "conference_messages": [],
        "date": None,
    })

    current_date = None
    in_conference = False

    # feed_history 是 newest-first，需要反转
    for evt in reversed(feed_history):
        evt_type = evt.get("type", "")

        # 从 day_start 推断日期
        if evt_type == "day_start":
            current_date = evt.get("date") or evt.get("timestamp", "")[:10]
            by_date[current_date]["date"] = current_date

        if not current_date:
            # 尝试从 timestamp 推断
            ts = evt.get("timestamp") or evt.get("ts")
            if isinstance(ts, (int, float)) and ts > 1e9:
                current_date = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            elif isinstance(ts, str) and len(ts) >= 10:
                current_date = ts[:10]

        if not current_date:
            continue

        if evt_type == "conference_start":
            in_conference = True
        elif evt_type == "conference_end":
            in_conference = False
        elif evt_type == "agent_message":
            aid = evt.get("agentId", "")
            content = evt.get("content", "")
            if aid and content:
                # 同一天同一 agent 可能有多条（取最后一条/最长一条）
                existing = by_date[current_date]["agent_messages"].get(aid, "")
                if len(content) > len(existing):
                    by_date[current_date]["agent_messages"][aid] = content
        elif evt_type == "conference_message":
            by_date[current_date]["conference_messages"].append(evt)

    return dict(by_date)


def build_pnl_summary(trades_data: list[dict], date: str) -> str:
    day_trades = [t for t in trades_data if t.get("date", "")[:10] == date]
    if not day_trades:
        return f"No trades executed on {date}."
    lines = [f"Settlement for {date}:"]
    total = 0.0
    for t in day_trades:
        pnl = t.get("pnl", 0) or 0
        total += pnl
        sign = "+" if pnl >= 0 else ""
        lines.append(f"  {t['ticker']}: {t.get('action','').upper()} {t.get('quantity',0)} @ ¥{t.get('price',0):.2f} | P&L {sign}¥{pnl:.2f}")
    sign = "+" if total >= 0 else ""
    lines.append(f"Total P&L: {sign}¥{total:.2f}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="evo2025_q1", help="回测配置名（与 run_backtest.py --config 一致）")
    ap.add_argument("--max-days", type=int, default=0, help="最多输出几天（0=全部）")
    ap.add_argument("--feed-output", type=Path,
                    default=ROOT / "frontend" / "public" / "demo_feed.json")
    ap.add_argument("--dashboard-output", type=Path,
                    default=ROOT / "frontend" / "public" / "demo_dashboard.json")
    ap.add_argument("--summarize", action="store_true",
                    help="用 DeepSeek 把每条发言提炼成中文重点（带缓存，重跑不重复付费）")
    args = ap.parse_args()

    global _SUMM_ENABLED
    if args.summarize:
        _SUMM_ENABLED = True
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
        _load_summary_cache(args.config)
        print(f"🤖 中文总结已开启（缓存命中 {len(_SUMM_CACHE)} 条）")

    dash_dir = ROOT / args.config / "team_dashboard"
    # StateSync 把完整 feed 写在 {config}/state/server_state.json
    state_file = ROOT / args.config / "state" / "server_state.json"
    if not state_file.exists():
        # 兼容旧路径
        state_file = dash_dir / "_internal_state.json"

    if not state_file.exists():
        raise SystemExit(
            f"❌ 找不到 server_state.json（{ROOT / args.config / 'state'}）\n"
            f"请先运行回测：python run_backtest.py --config {args.config} --reset\n"
            f"（这次回测会自动保存完整的 agent 推理文字）"
        )

    state = read_json(state_file, {})
    feed_history = state.get("feed_history", [])
    if not feed_history:
        raise SystemExit(
            f"❌ {state_file} 的 feed_history 为空。\n"
            f"请确保回测版本已加入 StateSync（run_backtest.py 加了 state_sync 参数后重跑）。"
        )

    print(f"✅ Loaded {len(feed_history)} events from {state_file}")

    leaderboard = read_json(dash_dir / "leaderboard.json", [])
    trades_raw  = read_json(dash_dir / "trades.json", [])

    # 按日期分组
    by_date = group_by_date(feed_history)
    all_dates = sorted(by_date.keys())
    if args.max_days > 0:
        all_dates = all_dates[-args.max_days:]

    print(f"📅 Days to export: {all_dates}")

    all_events: list[dict] = []
    for idx, date in enumerate(all_dates):
        day_data = by_date[date]
        pnl = build_pnl_summary(trades_raw, date)
        day_trades = [t for t in trades_raw if t.get("date", "")[:10] == date]
        day_events = build_events_for_day(
            date=date,
            day_idx=idx,
            agent_messages=day_data["agent_messages"],
            conference_messages=day_data["conference_messages"],
            trades=day_trades,
            pnl_summary=pnl,
        )
        all_events.extend(day_events)
        agents_found = list(day_data["agent_messages"].keys())
        conf_msgs = len(day_data["conference_messages"])
        print(f"  {date}: {len(agents_found)} agent reports, {conf_msgs} conf msgs, {len(day_trades)} trades → {len(day_events)} events")

    feed_out = {
        "source": f"{args.config}/team_dashboard/_internal_state.json",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(all_events),
        "days": all_dates,
        "phases": PHASES,
        "leaderboard": leaderboard,
        "events": all_events,
    }
    args.feed_output.parent.mkdir(parents=True, exist_ok=True)
    args.feed_output.write_text(json.dumps(feed_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ demo_feed.json → {args.feed_output}  ({len(all_events)} events, {len(all_dates)} days)")

    # dashboard 保持不变（用已有脚本生成的即可）
    summary = read_json(dash_dir / "summary.json", {})
    stats   = read_json(dash_dir / "stats.json", {})
    holdings = read_json(dash_dir / "holdings.json", [])
    dashboard_out = {
        "source": str(dash_dir.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio": {
            "total_value": summary.get("totalAssetValue") or summary.get("balance"),
            "pnl_percent": summary.get("pnlPct") or summary.get("totalReturn") or stats.get("totalReturn"),
            "equity":      summary.get("equity", []),
            "baseline":    summary.get("baseline", []),
            "baseline_vw": summary.get("baseline_vw", []),
            "momentum":    summary.get("momentum", []),
        },
        "dashboard": {"stats": stats or summary, "holdings": holdings,
                      "trades": trades_raw, "leaderboard": leaderboard},
    }
    args.dashboard_output.write_text(json.dumps(dashboard_out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ demo_dashboard.json → {args.dashboard_output}")

    if _SUMM_ENABLED:
        _save_summary_cache()
        print(f"💾 总结缓存已保存（{len(_SUMM_CACHE)} 条）→ {_SUMM_CACHE_PATH}")


if __name__ == "__main__":
    main()
