# backtest_202402_regime_pm_b_smoke_v2 Backtest Attribution

## Key Results

- Agent final equity: ¥505,577.11 (1.12%)
- Equal-weight benchmark: ¥509,894.51 (1.98%)
- Momentum benchmark: ¥509,955.30 (1.99%)
- Excess vs equal weight: ¥-4,317.40

## Attribution Read

- Cash/risk-control effect vs equal-weight benchmark: ¥-9,894.51. This is the loss the equal-weight benchmark took while the agent kept a large cash reserve.
- Active portfolio PnL over cash: ¥5,577.11. This is what the actual stock decisions added after starting from cash.
- Gross trade PnL by ticker sums to ¥5,674.00; execution/cost/slippage drag is approximately ¥-96.89.

## Ticker PnL

| Ticker | Buy Value | Sell Value | Final Value | Approx PnL |
|---|---:|---:|---:|---:|
| 000333.SZ | ¥38,332.00 | ¥0.00 | ¥37,898.00 | ¥-434.00 |
| 000858.SZ | ¥122,450.00 | ¥0.00 | ¥127,600.00 | ¥5,150.00 |
| 300750.SZ | ¥60,500.00 | ¥0.00 | ¥59,984.00 | ¥-516.00 |
| 600030.SH | ¥24,404.00 | ¥0.00 | ¥24,516.00 | ¥112.00 |
| 600276.SH | ¥24,726.00 | ¥0.00 | ¥25,116.00 | ¥390.00 |
| 601398.SH | ¥37,827.00 | ¥0.00 | ¥38,799.00 | ¥972.00 |

## Final Weights

| Asset | Weight |
|---|---:|
| CASH | 37.92% |
| 000858.SZ | 25.23% |
| 300750.SZ | 11.86% |
| 601398.SH | 7.67% |
| 000333.SZ | 7.49% |
| 600276.SH | 4.97% |
| 600030.SH | 4.85% |

## Charts

- `equity_vs_benchmarks.png`: agent vs equal-weight and momentum benchmarks.
- `drawdown.png`: drawdown comparison.
- `portfolio_weights.png`: daily cash and position weights.
- `pnl_attribution.png`: approximate ticker-level PnL.

## Interpretation

The agent made positive absolute PnL, but high cash became a drag because the benchmark rose. This is a missed-upside result: the PM was prudent, but too conservative for this window.