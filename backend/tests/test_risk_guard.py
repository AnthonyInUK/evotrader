# -*- coding: utf-8 -*-
from unittest.mock import MagicMock

import pytest
from agentscope.message import Msg

from backend.core.pipeline import TradingPipeline
from backend.core.risk_guard import RiskGuard, SECTOR_MAP


def test_experiment_pool_has_explicit_sectors():
    tickers = {
        "600519.SH",
        "601398.SH",
        "000858.SZ",
        "000333.SZ",
        "300750.SZ",
        "600030.SH",
        "601318.SH",
        "600276.SH",
    }

    assert tickers.issubset(SECTOR_MAP.keys())


def test_sector_guard_does_not_bucket_pool_as_other():
    guard = RiskGuard(
        position_limit=0.30,
        drawdown_limit=0.05,
        sector_limit=0.60,
    )
    portfolio = {
        "cash": 266000.82,
        "positions": {
            "000858.SZ": {"long": 800},
            "300750.SZ": {"long": 900},
        },
    }
    open_prices = {
        "000858.SZ": 127.0,
        "300750.SZ": 150.0,
        "600030.SH": 20.0,
    }
    open_nav = (
        portfolio["cash"]
        + 800 * open_prices["000858.SZ"]
        + 900 * open_prices["300750.SZ"]
    )
    decisions = {
        "600030.SH": {
            "action": "long",
            "quantity": 1000,
            "confidence": 70,
        }
    }

    filtered = guard.apply(decisions, portfolio, open_prices, open_nav)

    assert filtered["600030.SH"]["action"] == "long"
    assert filtered["600030.SH"]["quantity"] == 1000


def test_pipeline_exposes_risk_guard_context_to_pm():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )

    context = pipeline._risk_guard_context(["600519.SH", "600030.SH"])

    assert context["single_position_limit_pct"] == 30.0
    assert context["sector_limit_pct"] == 60.0
    assert context["a_share_lot_size"] == 100
    assert context["known_sectors"]["600519.SH"] == "消费"
    assert context["known_sectors"]["600030.SH"] == "金融"
    assert "clipped to zero" in context["note"]


def test_pipeline_compacts_pm_context(monkeypatch):
    monkeypatch.setenv("PM_CONTEXT_MODE", "compact")
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )
    long_fundamental_text = (
        "SIGNAL: BULL | CONFIDENCE: 82 | TICKER: 000858.SZ\n"
        "000858.SZ fundamentals are strong. "
        + "very long explanation " * 80
    )
    technical_text = (
        "SIGNAL: BEAR | CONFIDENCE: 70 | TICKER: 000858.SZ\n"
        "000858.SZ price below SMA20 and SMA60."
    )
    analyst_results = [
        {"agent": "fundamentals_analyst", "content": long_fundamental_text},
        {"agent": "technical_analyst", "content": technical_text},
        {"agent": "sentiment_analyst", "content": "Analysis failed. Reason: 180s timeout"},
    ]
    risk_assessment = {
        "content": "Risk score low, but concentration should be monitored."
    }

    context = pipeline._analyst_context_for_pm(
        ["000858.SZ"],
        analyst_results,
        risk_assessment,
    )

    assert "analyst_signals" not in context
    facts = context["analyst_signal_facts"]
    ticker_facts = facts["by_ticker"]["000858.SZ"]
    assert ticker_facts["signals"]["fundamentals_analyst"]["signal"] == "BULL"
    assert ticker_facts["signals"]["technical_analyst"]["signal"] == "BEAR"
    assert "000858.SZ" in facts["conflict_tickers"]
    assert facts["missing_or_failed_agents"][0]["agent"] == "sentiment_analyst"
    assert len(ticker_facts["excerpts"]["fundamentals_analyst"]) < 260
    assert facts["raw_context_stats"]["analyst_text_bytes"]["fundamentals_analyst"] > 1000
    assert context["risk_warnings"].startswith("Risk score low")


def test_pipeline_compact_signal_parser_accepts_aliases():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )

    signals = pipeline._parse_signal_lines(
        "\n".join(
            [
                "SIGNAL: BULLISH | CONFIDENCE: 60 | TICKER: 601398.SH",
                "SIGNAL: BEARISH | CONFIDENCE: 70 | TICKER: 000858.SZ",
                "SIGNAL: UP | CONFIDENCE: 55 | TICKER: 000333.SZ",
                "SIGNAL: DOWN | CONFIDENCE: 65 | TICKER: 600030.SH",
            ]
        )
    )

    assert signals["601398.SH"]["signal"] == "BULL"
    assert signals["000858.SZ"]["signal"] == "BEAR"
    assert signals["000333.SZ"]["signal"] == "BULL"
    assert signals["600030.SH"]["signal"] == "BEAR"


def test_pipeline_compact_signal_parser_accepts_markdown_table():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )

    signals = pipeline._parse_signal_lines(
        "\n".join(
            [
                "| Ticker | 公司 | 信号 | 置信度 |",
                "|--------|------|:----:|:-----:|",
                "| 600519.SH | 贵州茅台 | **🟢 BULL** | **85** |",
                "| 贵州茅台 | 600519.SH | **🟢 BULL** | **85** |",
                "| 601398.SH | 工商银行 | **🟡 NEUTRAL** | **55** |",
                "| 600030.SH | 中信证券 | **🔴 BEAR** | **65** |",
            ]
        )
    )

    assert signals["600519.SH"]["signal"] == "BULL"
    assert signals["600519.SH"]["confidence"] == 85
    assert signals["601398.SH"]["signal"] == "NEUTRAL"
    assert signals["600030.SH"]["signal"] == "BEAR"


def test_pipeline_regime_evidence_downgrades_weak_technical_breadth():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )
    tickers = ["600519.SH", "601398.SH", "000333.SZ", "300750.SZ"]
    analyst_results = [
        {
            "agent": "technical_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BEAR | CONFIDENCE: 65 | TICKER: 600519.SH",
                    "SIGNAL: BEAR | CONFIDENCE: 60 | TICKER: 601398.SH",
                    "SIGNAL: BEAR | CONFIDENCE: 70 | TICKER: 000333.SZ",
                    "SIGNAL: BULL | CONFIDENCE: 55 | TICKER: 300750.SZ",
                ]
            ),
        },
        {
            "agent": "fundamentals_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BULL | CONFIDENCE: 80 | TICKER: 600519.SH",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 55 | TICKER: 601398.SH",
                    "SIGNAL: BULL | CONFIDENCE: 75 | TICKER: 000333.SZ",
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 300750.SZ",
                ]
            ),
        },
        {
            "agent": "sentiment_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: NEUTRAL | CONFIDENCE: 50 | TICKER: 600519.SH",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 50 | TICKER: 601398.SH",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 50 | TICKER: 000333.SZ",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 50 | TICKER: 300750.SZ",
                ]
            ),
        },
    ]

    evidence = pipeline._build_regime_evidence(
        tickers,
        analyst_results,
        {"content": "Risk score: 12 / 100"},
    )

    assert evidence["technical_breadth"] == "weak"
    assert evidence["max_allowed_regime"] == "WEAK"
    assert evidence["suggested_regime"] == "WEAK"
    assert evidence["target_exposure_band"]["high_pct"] == 60.0
    assert "technical_breadth_weak" in evidence["downgrade_reasons"]


def test_pipeline_regime_evidence_does_not_treat_moderate_concentration_as_market_weak():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )
    tickers = ["600519.SH", "601398.SH", "000333.SZ", "300750.SZ"]
    analyst_results = [
        {
            "agent": "technical_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 600519.SH",
                    "SIGNAL: BULL | CONFIDENCE: 65 | TICKER: 601398.SH",
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 000333.SZ",
                    "SIGNAL: BULL | CONFIDENCE: 65 | TICKER: 300750.SZ",
                ]
            ),
        },
        {
            "agent": "fundamentals_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BULL | CONFIDENCE: 80 | TICKER: 600519.SH",
                    "SIGNAL: BULL | CONFIDENCE: 75 | TICKER: 601398.SH",
                    "SIGNAL: BULL | CONFIDENCE: 80 | TICKER: 000333.SZ",
                    "SIGNAL: BULL | CONFIDENCE: 75 | TICKER: 300750.SZ",
                ]
            ),
        },
        {
            "agent": "sentiment_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 600519.SH",
                    "SIGNAL: BULL | CONFIDENCE: 65 | TICKER: 601398.SH",
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 000333.SZ",
                    "SIGNAL: BULL | CONFIDENCE: 65 | TICKER: 300750.SZ",
                ]
            ),
        },
    ]

    evidence = pipeline._build_regime_evidence(
        tickers,
        analyst_results,
        {
            "content": (
                "集中度风险偏高，但最终风险评分：45/100 — 中风险。"
            )
        },
    )

    assert evidence["technical_breadth"] == "strong"
    assert evidence["risk_elevated"] is False
    assert evidence["suggested_regime"] == "REBOUND"
    assert evidence["max_allowed_regime"] == "REBOUND"


def test_pipeline_structured_decision_ranks_rebound_beta_candidates():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )
    tickers = ["600030.SH", "601318.SH", "000333.SZ", "600276.SH"]
    analyst_results = [
        {
            "agent": "technical_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BULL | CONFIDENCE: 75 | TICKER: 600030.SH",
                    "SIGNAL: BULL | CONFIDENCE: 75 | TICKER: 601318.SH",
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 000333.SZ",
                    "SIGNAL: BULL | CONFIDENCE: 65 | TICKER: 600276.SH",
                ]
            ),
        },
        {
            "agent": "fundamentals_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BEAR | CONFIDENCE: 70 | TICKER: 600030.SH",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 60 | TICKER: 601318.SH",
                    "SIGNAL: BULL | CONFIDENCE: 75 | TICKER: 000333.SZ",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 55 | TICKER: 600276.SH",
                ]
            ),
        },
        {
            "agent": "sentiment_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 600030.SH",
                    "SIGNAL: BULL | CONFIDENCE: 65 | TICKER: 601318.SH",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 55 | TICKER: 000333.SZ",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 50 | TICKER: 600276.SH",
                ]
            ),
        },
        {
            "agent": "valuation_analyst",
            "content": "\n".join(
                [
                    "SIGNAL: BEAR | CONFIDENCE: 65 | TICKER: 600030.SH",
                    "SIGNAL: BEAR | CONFIDENCE: 60 | TICKER: 601318.SH",
                    "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 000333.SZ",
                    "SIGNAL: NEUTRAL | CONFIDENCE: 50 | TICKER: 600276.SH",
                ]
            ),
        },
    ]

    structured = pipeline._build_structured_decision_layer(
        tickers,
        analyst_results,
        {"content": "Risk score: 15 / 100"},
        {
            "600030.SH": 20.0,
            "601318.SH": 40.0,
            "000333.SZ": 55.0,
            "600276.SH": 42.0,
        },
        {"cash": 500000, "positions": {}},
    )

    ranked = {row["ticker"]: row for row in structured["candidate_scores"]}
    assert structured["regime"] == "REBOUND"
    assert ranked["600030.SH"]["role"] == "brokerage_beta"
    assert ranked["601318.SH"]["role"] == "insurance_repair"
    assert ranked["600030.SH"]["score"] > ranked["600276.SH"]["score"]
    assert ranked["601318.SH"]["score"] > ranked["600276.SH"]["score"]
    assert ranked["600030.SH"]["suggested_qty"] >= 100


def test_pipeline_compact_reasoning_log_drops_tool_trace_noise():
    pipeline = TradingPipeline(
        analysts=[],
        risk_manager=MagicMock(),
        portfolio_manager=MagicMock(),
    )
    msgs = [
        Msg(
            name="technical_analyst",
            role="assistant",
            content=(
                "{'type': 'tool_use', 'name': 'history_calculate'}\n"
                "{'type': 'tool_result', 'output': '"
                + "very long raw kline data " * 100
                + "'}\n"
                "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 000333.SZ\n"
                "- Evidence: price above SMA20 and SMA60.\n"
                "SUMMARY: Technical breadth is improving."
            ),
        )
    ]

    lines = pipeline._summarize_trajectory_for_log("technical_analyst", msgs)
    text = "\n".join(lines)

    assert "tool_use_count: 1" in text
    assert "tool_result_count: 1" in text
    assert "SIGNAL: BULL | CONFIDENCE: 70 | TICKER: 000333.SZ" in text
    assert "SUMMARY: Technical breadth is improving." in text
    assert "very long raw kline data" not in text
