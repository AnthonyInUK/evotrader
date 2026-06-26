# -*- coding: utf-8 -*-
"""
测试 PM 工具调用 + 压缩机制
验证 Qwen 在 context 压缩后能否稳定调用 _make_decision
"""
import asyncio, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
sys.path.insert(0, str(Path(__file__).parent))

from agentscope.message import Msg
from backend.agents import PMAgent
from backend.llm.models import get_agent_model, get_agent_formatter


async def test():
    pm = PMAgent(
        model=get_agent_model("portfolio_manager"),
        formatter=get_agent_formatter("portfolio_manager"),
        initial_cash=100000.0,
    )

    fake_context = """
Based on analyst signals, make decisions for 2024-01-02.

Analyst Signals:
- AAPL: Bullish (confidence 85), strong fundamentals
- BABA: Neutral (confidence 70), regulatory risk
- SPY:  Bullish (confidence 80), broad market growth
- TSLA: Bullish (confidence 75), EV leadership

Current prices: AAPL=$185, BABA=$76, SPY=$465, TSLA=$250
Cash available: $100,000
Positions: none

Use _make_decision tool for each ticker: AAPL, BABA, SPY, TSLA.
Call it once per ticker with explicit arguments.
"""
    msg = Msg(name="system", content=fake_context, role="user")
    result = await pm.reply(
        msg,
        tickers=["AAPL", "BABA", "SPY", "TSLA"],
        prices={"AAPL": 185, "BABA": 76, "SPY": 465, "TSLA": 250},
    )

    decisions = pm.get_decisions()

    print("\n--- 结果 ---")
    if decisions:
        print("✅ _make_decision 工具调用成功！")
        total_cost = 0
        prices = {"AAPL": 185, "BABA": 76, "SPY": 465, "TSLA": 250}
        for ticker, d in decisions.items():
            action = d.get("action", "?").upper()
            qty    = d.get("quantity", 0)
            conf   = d.get("confidence", 0)
            cost   = qty * prices.get(ticker, 0) if action != "HOLD" else 0
            total_cost += cost
            print(f"  {ticker}: {action} {qty}股 (信心{conf}%) ~${cost:,.0f}")
        print(f"  总资金使用: ${total_cost:,.0f} / $100,000")
    else:
        print("❌ _make_decision 未被调用，decisions 为空")
        print("   PM 原始输出:", result.content[:300] if result.content else "无")


asyncio.run(test())
