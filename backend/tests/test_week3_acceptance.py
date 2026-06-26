# -*- coding: utf-8 -*-
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_week3_acceptance_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "week3_acceptance.py"
    )
    spec = spec_from_file_location("week3_acceptance", script_path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_stats_from_summary():
    week3 = _load_week3_acceptance_module()

    summary = {
        "totalAssetValue": 98086.89,
        "totalReturn": -1.91,
        "cashPosition": 52.02,
        "tickerWeights": {"AAPL": 0.49},
        "totalTrades": 4,
        "equity": [
            {"t": 1704153600000, "v": 100000.0},
            {"t": 1704240000000, "v": 99650.06},
            {"t": 1704326400000, "v": 98873.53},
            {"t": 1704412800000, "v": 98086.89},
        ],
        "baseline": [
            {"t": 1704153600000, "v": 100000.0},
            {"t": 1704240000000, "v": 100000.0},
            {"t": 1704326400000, "v": 100000.0},
            {"t": 1704412800000, "v": 100000.0},
        ],
        "baseline_vw": [
            {"t": 1704153600000, "v": 100000.0},
            {"t": 1704240000000, "v": 99900.0},
            {"t": 1704326400000, "v": 99800.0},
            {"t": 1704412800000, "v": 99700.0},
        ],
        "momentum": [
            {"t": 1704153600000, "v": 100000.0},
            {"t": 1704240000000, "v": 100500.0},
            {"t": 1704326400000, "v": 100300.0},
            {"t": 1704412800000, "v": 100900.0},
        ],
    }
    existing_stats = {
        "winRate": 0.4,
        "bullBear": {"bull": {"n": 1, "win": 1}, "bear": {"n": 1, "win": 0}},
    }

    stats = week3.build_stats_from_summary(summary, existing_stats)

    assert stats["period"]["tradingDays"] == 3
    assert "annualizedReturnPct" in stats["performance"]["agent"]
    assert "sharpe" in stats["performance"]["agent"]
    assert "equalWeight" in stats["performance"]["comparison"]
    assert stats["winRate"] == 0.4


def test_normalize_leaderboard_backfills_week3_schema():
    week3 = _load_week3_acceptance_module()

    leaderboard = [
        {
            "agentId": "technical_analyst",
            "name": "Technical Analyst",
            "rank": 2,
            "winRate": 0.55,
            "signals": [
                {
                    "ticker": "AAPL",
                    "signal": "bull",
                    "date": "2024-01-05",
                    "is_correct": True,
                },
            ],
        },
        {
            "agentId": "fundamentals_analyst",
            "name": "Fundamentals Analyst",
            "rank": 1,
            "winRate": 0.66,
            "signals": [],
        },
    ]

    normalized = week3.normalize_leaderboard(leaderboard)

    assert normalized[0]["weightedScore"] is not None
    assert "scoreBreakdown" in normalized[0]
    assert "performanceHistory" in normalized[0]
    ranked = [entry for entry in normalized if entry.get("rank") is not None]
    assert sorted(entry["rank"] for entry in ranked) == [1, 2]
