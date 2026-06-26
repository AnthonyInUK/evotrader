# backtest_202402_rebound_pool8_smoke Backtest Attribution

## Key Results

- Agent final equity: ¥509,427.12 (1.89%)
- Equal-weight benchmark: ¥509,894.51 (1.98%)
- Momentum benchmark: ¥509,955.30 (1.99%)
- Excess vs equal weight: ¥-467.39

## Attribution Read

- Cash/risk-control effect vs equal-weight benchmark: ¥-9,894.51. This is the loss the equal-weight benchmark took while the agent kept a large cash reserve.
- Active portfolio PnL over cash: ¥9,427.12. This is what the actual stock decisions added after starting from cash.
- Gross trade PnL by ticker sums to ¥9,585.00; execution/cost/slippage drag is approximately ¥-157.88.

## Ticker PnL

| Ticker | Buy Value | Sell Value | Final Value | Approx PnL |
|---|---:|---:|---:|---:|
| 000333.SZ | ¥120,472.00 | ¥28,005.00 | ¥92,038.00 | ¥-429.00 |
| 000858.SZ | ¥121,982.00 | ¥0.00 | ¥127,600.00 | ¥5,618.00 |
| 300750.SZ | ¥15,073.00 | ¥0.00 | ¥14,996.00 | ¥-77.00 |
| 600276.SH | ¥37,089.00 | ¥0.00 | ¥37,674.00 | ¥585.00 |
| 601398.SH | ¥99,576.00 | ¥0.00 | ¥103,464.00 | ¥3,888.00 |

## Final Weights

| Asset | Weight |
|---|---:|
| CASH | 26.26% |
| 000858.SZ | 25.04% |
| 601398.SH | 20.30% |
| 000333.SZ | 18.06% |
| 600276.SH | 7.39% |
| 300750.SZ | 2.94% |

## Charts

- `equity_vs_benchmarks.png`: agent vs equal-weight and momentum benchmarks.
- `drawdown.png`: drawdown comparison.
- `portfolio_weights.png`: daily cash and position weights.
- `pnl_attribution.png`: approximate ticker-level PnL.

## Interpretation

The agent made positive absolute PnL, but high cash became a drag because the benchmark rose. This is a missed-upside result: the PM was prudent, but too conservative for this window.