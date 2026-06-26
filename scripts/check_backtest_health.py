# -*- coding: utf-8 -*-
"""Backtest health checks for logs and dashboard artifacts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ERROR_PATTERNS = {
    "tool_error": re.compile(r"\[ERROR\]|Error in \w+"),
    "ticker_split": re.compile(r"Still need: \[['\"](?:SH|SZ|BJ)['\"]\]"),
    "unexpected_kwarg": re.compile(r"unexpected keyword argument"),
    "auth_error": re.compile(
        r"Authentication Fails|invalid api key|Error code:\s*401",
        re.I,
    ),
}

ADVISORY_PATTERNS = {
    "dsml_text": re.compile(r"DSML|<\|{0,2}.*invoke name=", re.I),
    "pm_max_iters_after_decisions": re.compile(
        r"failed to generate a response within maximum iterations|"
        r"reached maximum iterations",
        re.I,
    ),
}

REQUIRED_DASHBOARD_FILES = [
    "summary.json",
    "holdings.json",
    "trades.json",
    "stats.json",
    "leaderboard.json",
]


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _dashboard_dir(config_name: str) -> Path:
    path = Path(config_name)
    if path.name == "team_dashboard":
        return path
    return path / "team_dashboard"


def _dates_from_summary(summary: dict[str, Any]) -> list[str]:
    dates = []
    for point in summary.get("equity", []):
        label = point.get("date") or point.get("d")
        if label:
            dates.append(str(label))
            continue
        timestamp = point.get("t")
        if timestamp is not None:
            try:
                from datetime import datetime, timezone

                dt = datetime.fromtimestamp(int(timestamp) / 1000, tz=timezone.utc)
                dates.append(dt.date().isoformat())
            except Exception:
                continue
    return dates


def _date_range(start: str | None, end: str | None) -> list[str]:
    if not start and not end:
        return []
    if start and not end:
        return [start]
    if end and not start:
        return [end]

    from datetime import date, timedelta

    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    days = []
    current = start_date
    while current <= end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def check_backtest(
    config_name: str,
    logs_dir: Path,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    dashboard_dir = _dashboard_dir(config_name)
    if str(logs_dir) == "logs":
        config_logs_dir = Path(config_name) / "logs"
        if config_logs_dir.exists():
            logs_dir = config_logs_dir
    files = {
        name: dashboard_dir / name
        for name in REQUIRED_DASHBOARD_FILES
    }
    missing_files = [name for name, path in files.items() if not path.exists()]

    summary = _load_json(files["summary.json"], {})
    holdings = _load_json(files["holdings.json"], [])
    trades = _load_json(files["trades.json"], [])
    stats = _load_json(files["stats.json"], {})
    leaderboard = _load_json(files["leaderboard.json"], [])

    summary_dates = _dates_from_summary(summary)
    if start or end:
        if summary_dates:
            start_bound = start or min(summary_dates)
            end_bound = end or max(summary_dates)
            dates = [
                d for d in summary_dates
                if start_bound <= d <= end_bound
            ]
        else:
            dates = _date_range(start, end)
    else:
        dates = summary_dates

    log_findings = []
    log_advisories = []
    seen_findings = set()
    seen_advisories = set()
    signal_count = 0
    recorded_count = 0
    completed_pm_count = 0
    for date in dates:
        log_path = logs_dir / "daily" / f"{date}_reasoning.txt"
        if not log_path.exists():
            log_findings.append(
                {"date": date, "kind": "missing_log", "message": str(log_path)}
            )
            continue
        text = log_path.read_text(encoding="utf-8", errors="replace")
        signal_count += len(re.findall(r"^SIGNAL:", text, flags=re.MULTILINE))
        recorded_count += len(re.findall(r"Recorded:", text))
        recorded_count += len(
            re.findall(
                r"^\s+[0-9A-Z]{6}\.(?:SH|SZ|BJ):\s+"
                r"(?:HOLD|LONG|SHORT)\s+\d+股",
                text,
                flags=re.MULTILINE,
            )
        )
        completed_pm_count += len(re.findall(r"All \d+ tickers decided", text))
        for kind, pattern in ERROR_PATTERNS.items():
            for match in pattern.finditer(text):
                start = max(match.start() - 100, 0)
                end = min(match.end() + 160, len(text))
                message = text[start:end].replace("\n", " ")[:260]
                signature = (date, kind, message[:80])
                if signature in seen_findings:
                    continue
                seen_findings.add(signature)
                log_findings.append(
                    {"date": date, "kind": kind, "message": message}
                )
        for kind, pattern in ADVISORY_PATTERNS.items():
            for match in pattern.finditer(text):
                start = max(match.start() - 100, 0)
                end = min(match.end() + 160, len(text))
                message = text[start:end].replace("\n", " ")[:260]
                signature = (date, kind, message[:80])
                if signature in seen_advisories:
                    continue
                seen_advisories.add(signature)
                log_advisories.append(
                    {"date": date, "kind": kind, "message": message}
                )

    final_equity = None
    equity = summary.get("equity", [])
    if equity:
        final_equity = equity[-1].get("v")

    health = {
        "configName": config_name,
        "dashboardDir": str(dashboard_dir),
        "missingFiles": missing_files,
        "datesChecked": dates,
        "logFindings": log_findings,
        "logAdvisories": log_advisories,
        "signals": signal_count,
        "pmRecordedDecisions": recorded_count,
        "pmCompletedDays": completed_pm_count,
        "finalEquity": final_equity,
        "cashPosition": stats.get("cashPosition"),
        "totalReturn": stats.get("totalReturn"),
        "holdingsCount": len(holdings) if isinstance(holdings, list) else None,
        "tradesCount": len(trades) if isinstance(trades, list) else None,
        "leaderboardCount": (
            len(leaderboard) if isinstance(leaderboard, list) else None
        ),
    }
    health["ok"] = not missing_files and not log_findings
    return health


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_name")
    parser.add_argument("--logs-dir", default="logs")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--json-out")
    args = parser.parse_args()

    result = check_backtest(
        args.config_name,
        Path(args.logs_dir),
        start=args.start,
        end=args.end,
    )
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    status = "PASS" if result["ok"] else "WARN"
    print(f"=== Backtest Health: {status} ===")
    print(f"Dashboard: {result['dashboardDir']}")
    print(f"Dates checked: {len(result['datesChecked'])}")
    print(f"Signals: {result['signals']}")
    print(f"PM recorded decisions: {result['pmRecordedDecisions']}")
    print(f"Trades: {result['tradesCount']} | Holdings: {result['holdingsCount']}")
    print(f"Final equity: {result['finalEquity']}")
    if result["missingFiles"]:
        print("Missing files:", ", ".join(result["missingFiles"]))
    if result["logFindings"]:
        print("Findings:")
        for item in result["logFindings"][:20]:
            print(f"- {item['date']} [{item['kind']}] {item['message']}")
        if len(result["logFindings"]) > 20:
            print(f"... {len(result['logFindings']) - 20} more")
    if result["logAdvisories"]:
        print("Advisories:")
        for item in result["logAdvisories"][:10]:
            print(f"- {item['date']} [{item['kind']}] {item['message']}")
        if len(result["logAdvisories"]) > 10:
            print(f"... {len(result['logAdvisories']) - 10} more")


if __name__ == "__main__":
    main()
