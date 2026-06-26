# LinkedIn Post

---

I built a multi-agent stock trading system. On its best day, all four AI analysts agreed on a strong buy. The portfolio manager read their reports — and sold instead.

Here's the actual exchange from Feb 23, 2024:

**Fundamentals agent:**
> "ROE 34.2%, net margin 52.5%, revenue growth 18%. Pricing power + fortress balance sheet. Signal: Bullish — 95% confidence."

**Technical agent:**
> "Price above SMA20 and SMA60. MACD golden cross. 20-day momentum +6.9%. The bullish signal is fundamentally anchored and technically robust → high-conviction long."

**Sentiment agent:**
> "Moutai is the most held A-share by foreign investors via Stock Connect. RSI 63.4 reflects disciplined institutional accumulation, not retail chasing. Signal: Bullish."

Then the Portfolio Manager processed all of it and output:

> "SHORT 100 shares. Confidence: 90%.
> Position weight is 30.9% — violates the 30% concentration limit. DCF shows the stock is priced 32% above intrinsic value. Good company. Expensive stock. Full exit."

---

This is the behavior I was trying to design for.

A single LLM prompt given all this data would have produced a confident "BUY" — it would have averaged the signals and smoothed over the tension. The multi-agent setup forces each dimension (fundamentals, technicals, sentiment, risk, portfolio construction) to have a distinct voice. The PM doesn't inherit the analysts' conviction — it has to weigh it against position limits, valuation, and portfolio structure.

The system is built on [AgentScope](https://github.com/modelscope/agentscope). Its MsgHub primitive lets all agents share a live conversation thread — the sentiment agent can read what the technical agent just said before responding, the way an actual investment committee works. Every agent's full reasoning chain is logged to disk after each trading day, so when the system makes a bad call, you can trace exactly where the judgment broke down.

3-month backtest on Kweichow Moutai + ICBC (Jan–Mar 2024):
→ Agent strategy: +2.32% vs buy-and-hold: +0.50%

The Feb 23 sell call turned out to be right — NAV peaked that day and drifted down through March.

Still a prototype. There's look-ahead bias in the fundamental data, no slippage model, and the long-term memory module isn't fully connected yet. But watching agents disagree in structured ways is more interesting to me than the returns number.

#MultiAgent #LLM #QuantitativeFinance #AgentScope #AIEngineering
