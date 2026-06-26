# 2024-02 Rebound Attribution

Config: `backtest_202402_regime_compact_log_v2_rebound`

Window: 2024-02-20 to 2024-02-23

## Summary

The system ran correctly, but the portfolio underperformed the equal-weight benchmark in this rebound window.

| Metric | Agent | Equal Weight Benchmark | Difference |
|---|---:|---:|---:|
| Start Equity | 500,000.00 | 500,000.00 | - |
| End Equity | 503,721.98 | 509,894.51 | -6,172.53 |
| Return | +0.74% | +1.98% | -1.24% |
| Max Drawdown | -0.75% | -0.21% | -0.54 pp |

Main conclusion: the agent did make money, but it did not capture enough of the rebound. The gap came mostly from delayed/limited exposure and missing several rebound contributors, not from catastrophic stock-picking losses.

## Exposure Path

| Date | Equity | Cash | Stock Value | Stock Exposure |
|---|---:|---:|---:|---:|
| 2024-02-20 | 496,258.49 | 231,506.49 | 264,752.00 | 53.35% |
| 2024-02-21 | 505,406.65 | 168,692.65 | 336,714.00 | 66.62% |
| 2024-02-22 | 504,920.98 | 146,453.98 | 358,467.00 | 70.99% |
| 2024-02-23 | 503,721.98 | 118,611.98 | 385,110.00 | 76.45% |

For a confirmed rebound regime, target exposure should be closer to 80%-90%. PM moved in the right direction, but spent most of the window below rebound-level exposure.

## Actual Position Contribution

| Ticker | Final Qty | Cost | Final Value | P&L | Return |
|---|---:|---:|---:|---:|---:|
| 000858.SZ | 1,100 | 134,681.00 | 140,360.00 | +5,679.00 | +4.22% |
| 600276.SH | 1,200 | 49,452.00 | 50,232.00 | +780.00 | +1.58% |
| 300750.SZ | 900 | 136,133.00 | 134,964.00 | -1,169.00 | -0.86% |
| 000333.SZ | 1,100 | 61,005.00 | 59,554.00 | -1,451.00 | -2.38% |
| Total Stock P&L | - | 381,271.00 | 385,110.00 | +3,839.00 | +1.01% |

The selected stocks were not disastrous. Wuliangye carried the portfolio, Hengrui helped slightly, while CATL and Midea dragged.

## Benchmark Contributors Missed

Equal-weight benchmark component P&L:

| Ticker | Benchmark P&L | Return |
|---|---:|---:|
| 000858.SZ | +2,628.62 | +4.21% |
| 601398.SH | +2,440.35 | +3.90% |
| 600030.SH | +2,381.86 | +3.81% |
| 601318.SH | +2,082.26 | +3.33% |
| 600276.SH | +985.80 | +1.58% |
| 600519.SH | +616.31 | +0.99% |
| 300750.SZ | -533.06 | -0.85% |
| 000333.SZ | -707.63 | -1.13% |

The largest missed contributors were ICBC, CITIC Securities, and Ping An. PM held all three at zero throughout the window.

## Daily Gap

| Date | Agent Daily Return | Benchmark Daily Return | Gap |
|---|---:|---:|---:|
| 2024-02-20 | -0.75% | -0.04% | -0.71 pp |
| 2024-02-21 | +1.84% | +2.02% | -0.18 pp |
| 2024-02-22 | -0.10% | +0.20% | -0.30 pp |
| 2024-02-23 | -0.24% | -0.21% | -0.03 pp |

Most underperformance appeared on day one and day three.

## Signal Attribution

### PM

PM decision quality was not broken. It recorded all 32 decisions and increased exposure from 0% to 76.45%.

However, PM frequently classified the environment as `SIDEWAYS_OR_SMALL_UP` rather than `REBOUND`. That capped the target exposure at 65%-75% instead of pushing toward 80%-90%.

### Technical Analyst

Technical analyst was the strongest rebound detector in this short window:

- Bull signals: 25
- Correct bull signals: 19
- Win rate: 65.52%

It correctly identified rebound behavior in ICBC, CITIC Securities, and Ping An on multiple days.

### Fundamentals Analyst

Fundamentals analyst had a good measured win rate, but it stayed neutral on several rebound beta names:

- ICBC: neutral all four days
- Ping An: neutral all four days
- CITIC: mostly neutral, bearish on 2024-02-23

This is not necessarily wrong for a fundamental lens, but it means fundamentals dampened rebound participation.

### Valuation Analyst

Valuation analyst was conservative and often neutral. It was bearish/neutral on some rebound beta names:

- CITIC: bear/neutral
- Ping An: neutral/bear
- ICBC: mostly neutral except 2024-02-21

In rebound windows, valuation may help avoid value traps, but it can also suppress high-beta tactical trades.

### Sentiment Analyst

Sentiment analyst captured some rebound signals, but was inconsistent:

- ICBC: bull on 2024-02-20 and 2024-02-22
- CITIC: bull on 2024-02-21, but later mixed
- Ping An: bull only on 2024-02-22

It helped, but did not produce enough stable conviction for PM to allocate.

## Root Cause

This was not primarily an execution bug.

The underperformance came from:

1. PM recognized the rebound too slowly or too cautiously.
2. PM respected `SIDEWAYS_OR_SMALL_UP` target exposure for too long.
3. A-share lot size blocked Moutai repeatedly.
4. Fundamental and valuation analysts suppressed rebound beta stocks such as CITIC Securities and Ping An.
5. Cash remained useful as risk control, but costly in a rebound window.

## Decision

PM v2 is usable, but rebound behavior is not fully validated.

Do not immediately rewrite the full PM prompt. The next test should isolate one question:

When `regime_evidence` says the market is technically strong and broadening, should PM let technical/sentiment evidence temporarily outweigh fundamental/valuation objections for small tactical rebound positions?

Recommended next experiment:

- Keep PM core rules fixed.
- Add a narrowly scoped rebound-beta rule.
- Test only a short rebound window first.

