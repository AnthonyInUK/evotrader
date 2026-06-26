# backtest_202402_regime_pm_b_smoke_v4 Backtest Attribution

## Key Results

- Agent final equity: ¥505,441.94 (1.09%)
- Equal-weight benchmark: ¥509,894.51 (1.98%)
- Momentum benchmark: ¥509,955.30 (1.99%)
- Excess vs equal weight: ¥-4,452.57

## Attribution Read

- Cash/risk-control effect vs equal-weight benchmark: ¥-9,894.51. This is the loss the equal-weight benchmark took while the agent kept a large cash reserve.
- Active portfolio PnL over cash: ¥5,441.94. This is what the actual stock decisions added after starting from cash.
- Gross trade PnL by ticker sums to ¥5,568.00; execution/cost/slippage drag is approximately ¥-126.06.

## Ticker PnL

| Ticker | Buy Value | Sell Value | Final Value | Approx PnL |
|---|---:|---:|---:|---:|
| 000333.SZ | ¥109,520.00 | ¥0.00 | ¥108,280.00 | ¥-1,240.00 |
| 000858.SZ | ¥146,940.00 | ¥0.00 | ¥153,120.00 | ¥6,180.00 |
| 300750.SZ | ¥105,454.00 | ¥0.00 | ¥104,972.00 | ¥-482.00 |
| 600030.SH | ¥19,840.00 | ¥0.00 | ¥20,430.00 | ¥590.00 |
| 600276.SH | ¥32,968.00 | ¥0.00 | ¥33,488.00 | ¥520.00 |

## Final Weights

| Asset | Weight |
|---|---:|
| 000858.SZ | 30.29% |
| 000333.SZ | 21.42% |
| 300750.SZ | 20.76% |
| CASH | 16.87% |
| 600276.SH | 6.62% |
| 600030.SH | 4.04% |

## Charts

- `equity_vs_benchmarks.png`: agent vs equal-weight and momentum benchmarks.
- `drawdown.png`: drawdown comparison.
- `portfolio_weights.png`: daily cash and position weights.
- `pnl_attribution.png`: approximate ticker-level PnL.

## Interpretation

The agent made positive absolute PnL, but high cash became a drag because the benchmark rose. This is a missed-upside result: the PM was prudent, but too conservative for this window.