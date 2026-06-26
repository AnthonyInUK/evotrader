# backtest_202405_sideways_pool8_smoke Backtest Attribution

## Key Results

- Agent final equity: ¥500,849.57 (0.17%)
- Equal-weight benchmark: ¥505,316.56 (1.06%)
- Momentum benchmark: ¥502,083.95 (0.42%)
- Excess vs equal weight: ¥-4,466.99

## Attribution Read

- Cash/risk-control effect vs equal-weight benchmark: ¥-5,316.56. This is the loss the equal-weight benchmark took while the agent kept a large cash reserve.
- Active portfolio PnL over cash: ¥849.57. This is what the actual stock decisions added after starting from cash.
- Gross trade PnL by ticker sums to ¥934.00; execution/cost/slippage drag is approximately ¥-84.43.

## Ticker PnL

| Ticker | Buy Value | Sell Value | Final Value | Approx PnL |
|---|---:|---:|---:|---:|
| 000333.SZ | ¥124,013.00 | ¥0.00 | ¥122,531.00 | ¥-1,482.00 |
| 000858.SZ | ¥109,668.00 | ¥0.00 | ¥112,664.00 | ¥2,996.00 |
| 600276.SH | ¥44,770.00 | ¥0.00 | ¥44,190.00 | ¥-580.00 |

## Final Weights

| Asset | Weight |
|---|---:|
| CASH | 44.23% |
| 000333.SZ | 24.46% |
| 000858.SZ | 22.49% |
| 600276.SH | 8.82% |

## Charts

- `equity_vs_benchmarks.png`: agent vs equal-weight and momentum benchmarks.
- `drawdown.png`: drawdown comparison.
- `portfolio_weights.png`: daily cash and position weights.
- `pnl_attribution.png`: approximate ticker-level PnL.

## Interpretation

The agent made positive absolute PnL, but high cash became a drag because the benchmark rose. This is a missed-upside result: the PM was prudent, but too conservative for this window.