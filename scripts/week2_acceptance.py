#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Week 2 acceptance smoke for EvoTraders A-share toolization.

Checks:
1. Analyst / risk toolkits contain the expected tools.
2. Key Week-2 tools return non-error outputs on representative A-share inputs.
3. A minimal pipeline (sentiment + risk + PM) completes with fast model overrides.

Outputs:
- Prints a short summary to stdout
- Writes JSON report to local_test/week2_acceptance_report.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

os.environ.setdefault("AGENT_SENTIMENT_ANALYST_MODEL_NAME", "qwen-turbo")
os.environ.setdefault("AGENT_RISK_MANAGER_MODEL_NAME", "qwen-turbo")
os.environ.setdefault("AGENT_PORTFOLIO_MANAGER_MODEL_NAME", "qwen-turbo")

from backend.agents.analyst import AnalystAgent  # noqa: E402
from backend.agents.portfolio_manager import PMAgent  # noqa: E402
from backend.agents.risk_manager import RiskAgent  # noqa: E402
from backend.core.pipeline import TradingPipeline  # noqa: E402
from backend.llm.models import get_agent_formatter, get_agent_model  # noqa: E402
from backend.main import create_toolkit  # noqa: E402
from backend.tools.analysis_tools import (  # noqa: E402
    analyze_news_sentiment,
    crawl_ths_event,
    crawl_ths_finance,
    crawl_ths_news,
    crawl_ths_position,
    history_calculate,
)


def _tool_text(response) -> str:
    content = response.content[0]
    if hasattr(content, "text"):
        return content.text
    if isinstance(content, dict):
        return content.get("text", "")
    return str(content)


def _tool_ok(response) -> tuple[bool, str]:
    text = _tool_text(response)
    return ("[ERROR]" not in text, text)


def _toolkit_summary() -> dict:
    expectations = {
        "fundamentals_analyst": {"extract_entities_code", "crawl_ths_finance"},
        "technical_analyst": {"extract_entities_code", "history_calculate", "execute_code"},
        "sentiment_analyst": {"extract_entities_code", "crawl_ths_news", "crawl_ths_concept", "dashscope_search"},
    }

    summary = {}
    for analyst_type, expected in expectations.items():
        toolkit = create_toolkit(analyst_type)
        tool_names = set(toolkit.tools.keys())
        summary[analyst_type] = {
            "expected": sorted(expected),
            "actual": sorted(tool_names),
            "missing": sorted(expected - tool_names),
            "ok": expected.issubset(tool_names),
        }

    risk_agent = RiskAgent(
        model=get_agent_model("risk_manager"),
        formatter=get_agent_formatter("risk_manager"),
        name="risk_manager",
        config={"config_name": "week2_acceptance"},
    )
    risk_tools = set(risk_agent.toolkit.tools.keys())
    expected_risk = {"crawl_ths_holder", "crawl_ths_position", "crawl_ths_event"}
    summary["risk_manager"] = {
        "expected": sorted(expected_risk),
        "actual": sorted(risk_tools),
        "missing": sorted(expected_risk - risk_tools),
        "ok": expected_risk.issubset(risk_tools),
    }
    return summary


def _run_tool_smokes() -> list[dict]:
    cases = [
        ("crawl_ths_finance", lambda: crawl_ths_finance(["600519.SH"], "2024-03-29")),
        ("history_calculate", lambda: history_calculate(["300750.SZ"], "20240329")),
        ("crawl_ths_news", lambda: crawl_ths_news(["600519.SH"], "2024-03-29")),
        ("analyze_news_sentiment", lambda: analyze_news_sentiment(["600519.SH"], "2024-03-29")),
        (
            "crawl_ths_position",
            lambda: crawl_ths_position(
                portfolio={
                    "cash": 20000.0,
                    "margin_used": 5000.0,
                    "positions": {
                        "600519.SH": {"long": 10, "short": 0},
                        "300750.SZ": {"long": 5, "short": 0},
                    },
                },
                current_prices={"600519.SH": 1596.58, "300750.SZ": 190.50},
            ),
        ),
        ("crawl_ths_event", lambda: crawl_ths_event(["600519.SH"], "2024-03-29")),
    ]

    results = []
    for name, fn in cases:
        response = fn()
        ok, text = _tool_ok(response)
        results.append(
            {
                "tool": name,
                "ok": ok,
                "preview": text[:400],
            }
        )
    return results


async def _run_minimal_pipeline() -> dict:
    sentiment = AnalystAgent(
        analyst_type="sentiment_analyst",
        toolkit=create_toolkit("sentiment_analyst"),
        model=get_agent_model("sentiment_analyst"),
        formatter=get_agent_formatter("sentiment_analyst"),
        agent_id="sentiment_analyst",
        config={"config_name": "week2_acceptance"},
    )
    sentiment.max_iters = 2

    risk = RiskAgent(
        model=get_agent_model("risk_manager"),
        formatter=get_agent_formatter("risk_manager"),
        name="risk_manager",
        config={"config_name": "week2_acceptance"},
    )
    risk.max_iters = 2

    pm = PMAgent(
        name="portfolio_manager",
        model=get_agent_model("portfolio_manager"),
        formatter=get_agent_formatter("portfolio_manager"),
        initial_cash=100000.0,
        margin_requirement=0.0,
        config={"config_name": "week2_acceptance"},
    )
    pm.max_iters = 2

    pipeline = TradingPipeline(
        analysts=[sentiment],
        risk_manager=risk,
        portfolio_manager=pm,
        settlement_coordinator=None,
        max_comm_cycles=0,
    )

    result = await asyncio.wait_for(
        pipeline.run_cycle(
            tickers=["600519.SH"],
            date="2024-03-29",
            prices={"600519.SH": 1596.58},
            close_prices={"600519.SH": 1596.58},
            prev_closes={"600519.SH": 1580.00},
        ),
        timeout=90,
    )

    return {
        "ok": True,
        "result_keys": sorted(result.keys()),
        "pm_decisions": result.get("pm_decisions"),
        "executed_trades": result.get("executed_trades"),
        "risk_assessment_preview": str(result.get("risk_assessment", ""))[:500],
    }


def main() -> None:
    report = {
        "week": 2,
        "milestone": "Day 14 acceptance",
        "toolkits": _toolkit_summary(),
    }

    tool_smokes = _run_tool_smokes()
    report["tool_smokes"] = tool_smokes
    attempted = len(tool_smokes)
    succeeded = sum(1 for item in tool_smokes if item["ok"])
    report["tool_success_rate"] = succeeded / attempted if attempted else 0.0

    try:
        pipeline_result = asyncio.run(_run_minimal_pipeline())
    except Exception as exc:  # pragma: no cover - smoke failure is runtime-dependent
        pipeline_result = {"ok": False, "error": repr(exc)}
    report["minimal_pipeline"] = pipeline_result

    out_dir = ROOT / "local_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "week2_acceptance_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== Week 2 Acceptance ===")
    print(f"Toolkit groups checked: {len(report['toolkits'])}")
    print(f"Tool success rate: {report['tool_success_rate']:.0%}")
    print(f"Minimal pipeline ok: {report['minimal_pipeline'].get('ok')}")
    print(f"Report: {out_path}")


if __name__ == "__main__":
    main()
