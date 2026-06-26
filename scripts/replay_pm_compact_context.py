# -*- coding: utf-8 -*-
"""Replay PM compact-context parsing from saved reasoning logs.

This is a no-LLM diagnostic. It helps validate parser/context changes before
spending money on another backtest run.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from backend.core.pipeline import TradingPipeline


ANALYST_AGENTS = [
    "fundamentals_analyst",
    "technical_analyst",
    "sentiment_analyst",
    "valuation_analyst",
]

SECTION_RE = re.compile(
    r"^──\s+(?P<name>.+?)\s+推理过程\s+─+",
    flags=re.MULTILINE,
)

OLD_SIGNAL_RE = re.compile(
    r"SIGNAL:\s*(BULL|BEAR|NEUTRAL)\s*\|\s*"
    r"CONFIDENCE:\s*(\d+)\s*\|\s*TICKER:\s*([0-9A-Z.]+)",
    re.I,
)


class _DummyPM:
    def get_portfolio_state(self) -> dict[str, Any]:
        return {"cash": 0, "positions": {}}


def _date_range(start: str | None, end: str | None) -> list[str]:
    if start and not end:
        return [start]
    if end and not start:
        return [end]
    if not start and not end:
        return []

    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    days = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _default_dates(config_dir: Path) -> list[str]:
    daily_dir = config_dir / "logs" / "daily"
    if not daily_dir.exists():
        return []
    dates = []
    for path in sorted(daily_dir.glob("*_reasoning.txt")):
        dates.append(path.name.removesuffix("_reasoning.txt"))
    return dates


def _extract_sections(text: str) -> dict[str, str]:
    matches = list(SECTION_RE.finditer(text))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group("name").strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections[name] = text[start:end].strip()
    return sections


def _old_parse_signal_lines(text: str) -> dict[str, dict[str, Any]]:
    signals: dict[str, dict[str, Any]] = {}
    for match in OLD_SIGNAL_RE.finditer(text or ""):
        ticker = match.group(3).upper()
        signals[ticker] = {
            "signal": match.group(1).upper(),
            "confidence": int(match.group(2)),
        }
    return signals


def _missing_count(
    compact: dict[str, Any],
    tickers: list[str],
    agents: list[str],
) -> int:
    count = 0
    by_ticker = compact.get("by_ticker", {})
    for ticker in tickers:
        signals = by_ticker.get(ticker, {}).get("signals", {})
        for agent in agents:
            if signals.get(agent, {}).get("signal") == "MISSING":
                count += 1
    return count


def _signals_table(
    compact: dict[str, Any],
    tickers: list[str],
    agents: list[str],
) -> list[dict[str, Any]]:
    rows = []
    by_ticker = compact.get("by_ticker", {})
    for ticker in tickers:
        row: dict[str, Any] = {"ticker": ticker}
        signals = by_ticker.get(ticker, {}).get("signals", {})
        for agent in agents:
            item = signals.get(agent, {"signal": "MISSING", "confidence": 0})
            row[agent] = f"{item['signal']}({item['confidence']})"
        rows.append(row)
    return rows


def _build_with_parser(
    pipeline: TradingPipeline,
    tickers: list[str],
    analyst_texts: dict[str, str],
    risk_text: str,
    parser,
) -> dict[str, Any]:
    original_parser = TradingPipeline._parse_signal_lines
    try:
        TradingPipeline._parse_signal_lines = staticmethod(parser)
        analyst_results = [
            {"agent": agent, "content": analyst_texts.get(agent, "")}
            for agent in ANALYST_AGENTS
        ]
        return pipeline._build_compact_pm_context(
            tickers,
            analyst_results,
            {"content": risk_text},
        )
    finally:
        TradingPipeline._parse_signal_lines = staticmethod(original_parser)


def replay_date(config_dir: Path, day: str, tickers: list[str]) -> dict[str, Any]:
    log_path = config_dir / "logs" / "daily" / f"{day}_reasoning.txt"
    text = log_path.read_text(encoding="utf-8", errors="replace")
    sections = _extract_sections(text)
    analyst_texts = {agent: sections.get(agent, "") for agent in ANALYST_AGENTS}
    risk_text = sections.get("risk_manager", "")

    pipeline = TradingPipeline([], None, _DummyPM(), config_name=str(config_dir))
    old_compact = _build_with_parser(
        pipeline,
        tickers,
        analyst_texts,
        risk_text,
        _old_parse_signal_lines,
    )
    new_compact = _build_with_parser(
        pipeline,
        tickers,
        analyst_texts,
        risk_text,
        TradingPipeline._parse_signal_lines,
    )
    analyst_results = [
        {"agent": agent, "content": analyst_texts.get(agent, "")}
        for agent in ANALYST_AGENTS
    ]
    regime_evidence = pipeline._build_regime_evidence(
        tickers,
        analyst_results,
        {"content": risk_text},
    )

    old_bytes = len(json.dumps(old_compact, ensure_ascii=False).encode("utf-8"))
    new_bytes = len(json.dumps(new_compact, ensure_ascii=False).encode("utf-8"))
    raw_bytes = sum(len(v.encode("utf-8")) for v in analyst_texts.values())
    raw_bytes += len(risk_text.encode("utf-8"))

    return {
        "date": day,
        "logPath": str(log_path),
        "rawBytes": raw_bytes,
        "oldCompactBytes": old_bytes,
        "newCompactBytes": new_bytes,
        "oldMissingSignals": _missing_count(old_compact, tickers, ANALYST_AGENTS),
        "newMissingSignals": _missing_count(new_compact, tickers, ANALYST_AGENTS),
        "oldConflictTickers": old_compact.get("conflict_tickers", []),
        "newConflictTickers": new_compact.get("conflict_tickers", []),
        "missingOrFailedAgents": new_compact.get("missing_or_failed_agents", []),
        "regimeEvidence": regime_evidence,
        "signals": _signals_table(new_compact, tickers, ANALYST_AGENTS),
    }


def _print_report(result: dict[str, Any]) -> None:
    print("=== PM Compact Context Replay ===")
    print(f"Config: {result['configName']}")
    print(f"Dates: {', '.join(result['datesChecked'])}")
    print(f"Tickers: {', '.join(result['tickers'])}")
    print()

    for item in result["days"]:
        print(f"## {item['date']}")
        print(f"Raw analyst+risk bytes: {item['rawBytes']}")
        print(
            "Compact bytes: "
            f"old={item['oldCompactBytes']} new={item['newCompactBytes']}"
        )
        print(
            "Missing signals: "
            f"old={item['oldMissingSignals']} new={item['newMissingSignals']}"
        )
        print(
            "Conflict tickers: "
            f"old={item['oldConflictTickers']} new={item['newConflictTickers']}"
        )
        evidence = item["regimeEvidence"]
        counts = evidence["counts"]
        print(
            "Regime evidence: "
            f"suggested={evidence['suggested_regime']} "
            f"max={evidence['max_allowed_regime']} "
            f"target={evidence['target_exposure_band']['low_pct']:.0f}-"
            f"{evidence['target_exposure_band']['high_pct']:.0f}% "
            f"technical={evidence['technical_breadth']} "
            f"sentiment={evidence['sentiment_status']} "
            f"clean_bull={counts['clean_bullish_count']} "
            f"conflict={counts['conflict_count']} "
            f"bear_majority={counts['bearish_majority_count']} "
            f"downgrade={evidence['downgrade_reasons']}"
        )
        if item["missingOrFailedAgents"]:
            print(f"Unstructured agents: {item['missingOrFailedAgents']}")
        print("Signals:")
        for row in item["signals"]:
            print(
                "  "
                f"{row['ticker']}: "
                f"F={row['fundamentals_analyst']} "
                f"T={row['technical_analyst']} "
                f"S={row['sentiment_analyst']} "
                f"V={row['valuation_analyst']}"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay PM compact-context parsing without calling any LLM.",
    )
    parser.add_argument("config_name")
    parser.add_argument("--date", action="append")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--json-out")
    args = parser.parse_args()

    config_dir = Path(args.config_name)
    tickers = [item.strip().upper() for item in args.tickers.split(",") if item.strip()]
    dates = args.date or _date_range(args.start, args.end) or _default_dates(config_dir)
    if not dates:
        raise SystemExit("No dates found. Pass --date or --start/--end.")

    days = []
    for day in dates:
        log_path = config_dir / "logs" / "daily" / f"{day}_reasoning.txt"
        if not log_path.exists():
            raise SystemExit(f"Missing reasoning log: {log_path}")
        days.append(replay_date(config_dir, day, tickers))

    result = {
        "configName": args.config_name,
        "tickers": tickers,
        "datesChecked": dates,
        "days": days,
    }

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    _print_report(result)


if __name__ == "__main__":
    main()
