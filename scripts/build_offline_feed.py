"""Build a static Activity Feed replay from saved EvoTraders reasoning logs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = ROOT / "backtest_202402_regime_pm_b_smoke_v2" / "logs" / "daily"
DEFAULT_OUTPUT = ROOT / "frontend" / "public" / "demo_feed.json"
DEFAULT_DASHBOARD_OUTPUT = ROOT / "frontend" / "public" / "demo_dashboard.json"
DEFAULT_LEADERBOARD = ROOT / "backtest_202402_regime_pm_b_smoke_v2" / "team_dashboard" / "leaderboard.json"
DEFAULT_DASHBOARD_DIR = ROOT / "backtest_202402_regime_pm_b_smoke_v2" / "team_dashboard"

AGENT_ORDER = [
    "fundamentals_analyst",
    "technical_analyst",
    "sentiment_analyst",
    "valuation_analyst",
    "risk_manager",
    "portfolio_manager",
]

AGENT_NAMES = {
    "fundamentals_analyst": "Fundamentals Analyst",
    "technical_analyst": "Technical Analyst",
    "sentiment_analyst": "Sentiment Analyst",
    "valuation_analyst": "Valuation Analyst",
    "risk_manager": "Risk Manager",
    "portfolio_manager": "Portfolio Manager",
}

SECTION_RE = re.compile(r"^──\s+([a-z_]+)\s+推理过程\s+─+", re.MULTILINE)
FINAL_DECISION_RE = re.compile(
    r"── PM 最终决策 ─+\n(?P<body>.*?)(?=\n── [a-z_]+ 推理过程 ─+)",
    re.DOTALL,
)
SIGNAL_RE = re.compile(
    r"#{0,3}\s*SIGNAL:\s*(BULL|BEAR|NEUTRAL)\s*\|\s*CONFIDENCE:\s*(\d+)\s*\|\s*TICKER:\s*([0-9A-Z.]+)",
    re.IGNORECASE,
)


def strip_internal_trace(text: str) -> str:
    kept_lines = []
    skip_prefixes = (
        "{'type': 'thinking'",
        '{"type": "thinking"',
        "{'type': 'tool_use'",
        '{"type": "tool_use"',
        "{'type': 'tool_result'",
        '{"type": "tool_result"',
    )

    for line in text.splitlines():
        if line.strip().startswith(skip_prefixes):
            continue
        kept_lines.append(line)

    text = "\n".join(kept_lines)
    text = re.sub(r"\[offline replay truncated\]", "", text)
    return normalize_text(text)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dashboard-output", type=Path, default=DEFAULT_DASHBOARD_OUTPUT)
    parser.add_argument("--dashboard-dir", type=Path, default=DEFAULT_DASHBOARD_DIR)
    parser.add_argument("--leaderboard", type=Path, default=DEFAULT_LEADERBOARD)
    parser.add_argument("--max-days", type=int, default=4)
    return parser.parse_args()


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def trim_block(text: str, limit: int = 1800) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text

    boundary = text.rfind("\n\n", 0, limit)
    if boundary < limit * 0.55:
        boundary = limit
    return text[:boundary].rstrip() + "\n\n[offline replay truncated]"


def extract_agent_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}

    for idx, match in enumerate(matches):
        agent_id = match.group(1)
        start = match.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        sections[agent_id] = text[start:end].strip()

    return sections


def extract_final_decision(text: str) -> str:
    match = FINAL_DECISION_RE.search(text)
    if not match:
        return ""
    return match.group("body").strip()


def summarize_pm_decision(text: str) -> str:
    lines = []
    current_ticker = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        ticker_match = re.match(r"([0-9]{6}\.(?:SH|SZ)):\s+([A-Z]+)\s+([0-9]+股)\s+信心([0-9]+%)", line)
        if ticker_match:
            current_ticker = ticker_match.groups()
            continue

        if current_ticker and line.startswith("理由:"):
            ticker, action, shares, confidence = current_ticker
            reason = line.removeprefix("理由:").strip()
            reason = re.sub(r"\s+", " ", reason)
            if len(reason) > 220:
                reason = reason[:220].rstrip() + "..."
            lines.append(f"{ticker}: {action} {shares}, confidence {confidence}. {reason}")
            current_ticker = None

    if not lines:
        return trim_block(text)

    return "PM final decision\n" + "\n".join(lines)


def summarize_agent_section(agent_id: str, section: str) -> str:
    assistant_blocks = re.findall(r"\[assistant\]\n(.*?)(?=\n\[[a-z]+\]\n|\Z)", section, re.DOTALL)
    candidate = assistant_blocks[-1] if assistant_blocks else section
    candidate = strip_internal_trace(candidate)

    signals = SIGNAL_RE.findall(candidate)
    if signals:
        signal_lines = [f"{ticker}: {signal.upper()} {confidence}%" for signal, confidence, ticker in signals[:8]]
        header = "Signals: " + " | ".join(signal_lines)
        detail = strip_internal_trace(candidate)
        return trim_block(header + "\n\n" + detail, limit=1800)

    if agent_id == "risk_manager":
        risk_lines = []
        for line in candidate.splitlines():
            clean = line.strip()
            if re.search(r"risk|drawdown|exposure|position|limit|concentration", clean, re.I):
                risk_lines.append(clean)
            if len(risk_lines) >= 12:
                break
        if risk_lines:
            return trim_block("Risk review\n" + "\n".join(risk_lines), limit=1600)

    if not candidate:
        full_section = strip_internal_trace(section)
        signals = SIGNAL_RE.findall(full_section)
        if signals:
            signal_lines = [f"{ticker}: {signal.upper()} {confidence}%" for signal, confidence, ticker in signals[:8]]
            return "Signals: " + " | ".join(signal_lines)
        candidate = full_section

    return trim_block(candidate, limit=1600)


def date_to_base_ms(date_str: str) -> int:
    dt = datetime.fromisoformat(date_str).replace(hour=16, minute=0, second=0, tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def build_events_for_file(path: Path, day_index: int) -> list[dict]:
    date_str = path.name.replace("_reasoning.txt", "")
    text = path.read_text(encoding="utf-8")
    base_ms = date_to_base_ms(date_str) + day_index * 60_000
    conference_id = f"offline-{date_str}"

    events: list[dict] = [
        {
            "type": "day_start",
            "date": date_str,
            "content": f"Offline replay loaded for {date_str}",
            "timestamp": base_ms,
        },
        {
            "type": "conference_start",
            "conferenceId": conference_id,
            "title": f"A-share Daily Decision Meeting · {date_str}",
            "participants": AGENT_ORDER,
            "timestamp": base_ms + 1_000,
        },
    ]

    sections = extract_agent_sections(text)
    for offset, agent_id in enumerate(AGENT_ORDER, start=2):
        if agent_id == "portfolio_manager":
            final_decision = extract_final_decision(text)
            content = summarize_pm_decision(final_decision) if final_decision else summarize_agent_section(agent_id, sections.get(agent_id, ""))
        else:
            content = summarize_agent_section(agent_id, sections.get(agent_id, ""))

        if not content:
            continue

        events.append(
            {
                "type": "conference_message",
                "conferenceId": conference_id,
                "agentId": agent_id,
                "agentName": AGENT_NAMES[agent_id],
                "content": content,
                "timestamp": base_ms + offset * 1_000,
            }
        )

    events.extend(
        [
            {
                "type": "conference_end",
                "conferenceId": conference_id,
                "timestamp": base_ms + 10_000,
            },
            {
                "type": "day_complete",
                "date": date_str,
                "content": f"Offline replay complete: {date_str}",
                "timestamp": base_ms + 11_000,
            },
        ]
    )
    return events


def read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


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
        "dashboard": {
            "stats": stats or summary,
            "holdings": holdings,
            "trades": trades,
            "leaderboard": leaderboard,
        },
    }


def main() -> None:
    args = parse_args()
    log_files = sorted(args.log_dir.glob("*_reasoning.txt"))[-args.max_days :]
    if not log_files:
        raise SystemExit(f"No reasoning logs found in {args.log_dir}")

    events: list[dict] = []
    for day_index, path in enumerate(log_files):
        events.extend(build_events_for_file(path, day_index))

    events.sort(key=lambda event: event["timestamp"], reverse=True)
    leaderboard = []
    if args.leaderboard.exists():
        leaderboard = json.loads(args.leaderboard.read_text(encoding="utf-8"))

    payload = {
        "source": str(args.log_dir.relative_to(ROOT)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "event_count": len(events),
        "days": [path.name.replace("_reasoning.txt", "") for path in log_files],
        "leaderboard": leaderboard,
        "events": events,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(events)} events to {args.output}")

    dashboard_payload = build_dashboard_payload(args.dashboard_dir, leaderboard)
    args.dashboard_output.parent.mkdir(parents=True, exist_ok=True)
    args.dashboard_output.write_text(
        json.dumps(dashboard_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Wrote dashboard payload to {args.dashboard_output}")


if __name__ == "__main__":
    main()
