# -*- coding: utf-8 -*-
"""Tests for conference discussion: early-stop, prior-statement passing,
and transcript-grounded summary."""
import asyncio
import unittest
from unittest.mock import AsyncMock

from agentscope.message import Msg

from backend.core.pipeline import TradingPipeline


def _make_agent(name, replies):
    """A fake agent whose .reply returns queued Msg contents in order.

    `replies` is a list; each .reply call pops the next one.
    """
    agent = AsyncMock()
    agent.name = name
    queue = list(replies)

    async def reply(msg):
        text = queue.pop(0) if queue else f"{name} default"
        return Msg(name=name, content=text, role="assistant")

    agent.reply.side_effect = reply
    # Portfolio state is read synchronously by the PM discussion prompt builder.
    agent.get_portfolio_state = lambda: {
        "cash": 100000.0,
        "positions": {},
        "margin_used": 0.0,
        "margin_requirement": 0.25,
    }
    return agent


class TestConference(unittest.TestCase):
    def _build(self, analysts, pm):
        return TradingPipeline(
            analysts=analysts,
            risk_manager=_make_agent("risk", []),
            portfolio_manager=pm,
            state_sync=None,
            max_comm_cycles=3,
        )

    def test_prior_statements_passed_to_later_analysts(self):
        """The 2nd analyst's prompt should contain the 1st analyst's words."""
        all_prompts = []

        a1 = _make_agent("a1", ["A1 says BUY [STANCE: BULLISH]", "A1 cycle2 [STANCE: BULLISH]"])

        async def a2_reply(msg):
            all_prompts.append(msg.content)
            return Msg(name="a2", content="A2 responds [STANCE: BULLISH]", role="assistant")

        a2 = _make_agent("a2", [])
        a2.reply.side_effect = a2_reply

        pm = _make_agent("portfolio_manager", ["PM agenda", "PM agenda2", "summary"])
        pipe = self._build([a1, a2], pm)

        asyncio.run(
            pipe._run_conference_cycles(
                tickers=["AAPL"],
                date="2025-11-03",
                prices={"AAPL": 100.0},
                analyst_results=[],
                risk_assessment={},
            ),
        )

        # On cycle 0 a2 must have seen a1's words
        self.assertTrue(
            any("A1 says BUY" in p for p in all_prompts),
            f"Expected 'A1 says BUY' in one of {all_prompts}",
        )

    def test_early_stop_on_weighted_consensus(self):
        """Clear weighted consensus (BULLISH tags) stops after cycle 2 without LLM."""
        # Two analysts both say BULLISH → normalised score = 1.0 > 0.2 → stop
        a1 = _make_agent("a1", ["bears are wrong [STANCE: BULLISH]", "c2"])
        a2 = _make_agent("a2", ["strong upside [STANCE: BULLISH]", "c2"])
        pm = _make_agent("portfolio_manager", ["PM agenda cycle1", "PM agenda cycle2", "summary"])
        pipe = self._build([a1, a2], pm)
        # No settlement_coordinator → weights default to 0.5 each

        asyncio.run(
            pipe._run_conference_cycles(
                tickers=["AAPL"],
                date="2025-11-03",
                prices={"AAPL": 100.0},
                analyst_results=[],
                risk_assessment={},
            ),
        )

        # Stops after cycle 2 (first eligible check): PM runs agenda twice + summary = 3.
        # No LLM consensus call because weighted vote was decisive.
        self.assertEqual(pm.reply.call_count, 3)
        self.assertEqual(a1.reply.call_count, 2)

    def test_no_early_stop_on_cycle_zero(self):
        """Even with clear consensus, cycle 0 cannot trigger early stop."""
        a1 = _make_agent("a1", ["[STANCE: BULLISH]", "[STANCE: BULLISH]", "[STANCE: BULLISH]"])
        pm = _make_agent("portfolio_manager", ["PM agenda", "PM agenda2", "PM agenda3", "summary"])
        pipe = self._build([a1], pm)
        pipe.max_comm_cycles = 3

        asyncio.run(
            pipe._run_conference_cycles(
                tickers=["AAPL"],
                date="2025-11-03",
                prices={"AAPL": 100.0},
                analyst_results=[],
                risk_assessment={},
            ),
        )

        # Must run at least 2 cycles before stopping (cycle 0 skip + cycle 1 stops)
        self.assertGreaterEqual(a1.reply.call_count, 2)

    def test_summary_grounded_in_transcript(self):
        """The summary prompt must contain the actual analyst statements."""
        captured = {}
        a1 = _make_agent("a1", ["AAPL looks overvalued"])

        calls = {"n": 0}

        async def pm_reply(msg):
            calls["n"] += 1
            if calls["n"] == 1:
                return Msg(name="pm", content="agenda", role="assistant")
            if calls["n"] == 2:
                return Msg(name="pm", content="YES", role="assistant")
            captured["summary_prompt"] = msg.content
            return Msg(name="pm", content="final summary", role="assistant")

        pm = _make_agent("portfolio_manager", [])
        pm.reply.side_effect = pm_reply
        pipe = self._build([a1], pm)

        summary = asyncio.run(
            pipe._run_conference_cycles(
                tickers=["AAPL"],
                date="2025-11-03",
                prices={"AAPL": 100.0},
                analyst_results=[],
                risk_assessment={},
            ),
        )

        self.assertIn("AAPL looks overvalued", captured["summary_prompt"])
        self.assertEqual(summary, "final summary")


class TestTranscriptRendering(unittest.TestCase):
    """Token-budget-aware transcript rendering (context management)."""

    def _pipe(self):
        # 仅测纯函数，无需真实 agent
        return TradingPipeline.__new__(TradingPipeline)

    def test_under_budget_returns_verbatim(self):
        pipe = self._pipe()
        tx = [
            {"speaker": "technical_analyst", "content": "[STANCE: BULLISH] RSI low, buy"},
            {"speaker": "portfolio_manager", "content": "agreed"},
        ]
        out = pipe._render_transcript(tx, token_budget=6000)
        self.assertIn("agreed", out)
        self.assertNotIn("condensed", out)  # 未压缩

    def test_over_budget_condenses_but_keeps_recent_and_stance(self):
        pipe = self._pipe()
        tx = []
        for i in range(30):
            stance = ["BULLISH", "BEARISH", "NEUTRAL"][i % 3]
            tx.append({
                "speaker": f"analyst_{i % 4}",
                "content": f"[STANCE: {stance}] " + ("分析推理片段 " * 20) + f"turn{i}",
            })
        out = pipe._render_transcript(tx, token_budget=1500, recent_keep=6, head_chars=200)
        self.assertIn("condensed", out)          # 触发压缩
        self.assertIn("verbatim", out)           # 保留最近逐字段
        self.assertIn("turn29", out)             # 最新一轮逐字保留
        self.assertLessEqual(pipe._estimate_tokens(out), 1600)  # 受预算约束
        self.assertIn("[STANCE:", out)           # 关键信号未丢

    def test_single_huge_turn_is_truncated_within_budget(self):
        pipe = self._pipe()
        tx = [{"speaker": "a", "content": "[STANCE: BULLISH] " + ("x" * 20000)}]
        out = pipe._render_transcript(tx, token_budget=1000, recent_keep=6)
        self.assertLessEqual(pipe._estimate_tokens(out), 1100)
        self.assertIn("截断", out)


if __name__ == "__main__":
    unittest.main()
