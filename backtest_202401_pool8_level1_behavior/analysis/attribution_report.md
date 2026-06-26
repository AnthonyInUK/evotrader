# 2024-01 Pool8 Backtest Attribution

## Key Results

- Agent final equity: ¥504,051.56 (0.81%)
- Equal-weight benchmark: ¥481,019.76 (-3.80%)
- Momentum benchmark: ¥490,323.57 (-1.94%)
- Excess vs equal weight: ¥23,031.80

## Attribution Read

- Cash/risk-control effect vs equal-weight benchmark: ¥18,980.24. This is the loss the equal-weight benchmark took while the agent kept a large cash reserve.
- Active portfolio PnL over cash: ¥4,051.56. This is what the actual stock decisions added after starting from cash.
- Gross trade PnL by ticker sums to ¥4,272.00; execution/cost/slippage drag is approximately ¥-220.44.

## Ticker PnL

| Ticker | Buy Value | Sell Value | Final Value | Approx PnL |
|---|---:|---:|---:|---:|
| 000333.SZ | ¥99,220.00 | ¥0.00 | ¥105,800.00 | ¥6,580.00 |
| 000858.SZ | ¥126,874.00 | ¥34,554.00 | ¥90,976.00 | ¥-1,344.00 |
| 600276.SH | ¥40,580.00 | ¥40,096.00 | ¥0.00 | ¥-484.00 |
| 601398.SH | ¥59,920.00 | ¥8,440.00 | ¥51,000.00 | ¥-480.00 |

## Final Weights

| Asset | Weight |
|---|---:|
| CASH | 50.86% |
| 000333.SZ | 20.98% |
| 000858.SZ | 18.04% |
| 601398.SH | 10.11% |

## Charts

- `equity_vs_benchmarks.png`: agent vs equal-weight and momentum benchmarks.
- `drawdown.png`: drawdown comparison.
- `portfolio_weights.png`: daily cash and position weights.
- `pnl_attribution.png`: approximate ticker-level PnL.

## Interpretation

The result is mostly a risk-control win, not an aggressive stock-picking win. The agent beat the weak January benchmark primarily by staying around half in cash and avoiding the weakest names. Stock selection still added positive absolute PnL, mainly from 000333.SZ.