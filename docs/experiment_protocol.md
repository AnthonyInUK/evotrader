# EvoTraders Experiment Protocol

This document defines how we run backtest experiments without wasting LLM cost.
The goal is to expose obvious system and behavior problems early, then reserve
full-month runs for experiments that already passed short-window checks.

## Core Rule

Do not run a full-month experiment unless the experiment has a written card and
has passed short-window smoke checks.

Cost grows roughly with:

```text
number of tickers * number of agents * number of days * tool/LLM calls
```

For a 6-8 stock pool, a full-month run can quickly become expensive and noisy.
If a problem can be found in 1-5 trading days, stop there.

## Experiment Card

Every experiment must define these fields before running:

```text
Experiment name:
Goal:
Unique variable:
Fixed conditions:
Ticker pool:
Time window:
Expected behavior:
Failure conditions:
Can it run full month:
Metrics to compare:
```

If the unique variable is unclear, do not run the experiment.

## Run Levels

### Level 0: Code And Data Check

Purpose:

```text
Confirm the experiment can run without tool, schema, ticker, or logging errors.
```

Window:

```text
1 trading day
```

Do not judge:

```text
return, Sharpe, alpha
```

Stop if:

```text
data field mismatch
tool error
LLM code execution error
PM order quantity invalid
dashboard files not updated
```

### Level 1: Behavior Smoke

Purpose:

```text
Check whether analysts and PM behave as expected.
```

Window:

```text
3-5 trading days
```

Questions:

```text
Does PM buy when signals are strong?
Does PM stay defensive when signals are weak?
Does PM respect cash, single-name, T+1, circuit-breaker, and A-share lot rules?
Do analysts produce useful and stable views?
Does the system avoid repeated execute_code failures?
```

Stop if:

```text
PM has signals but never buys
PM buys aggressively without enough evidence
PM gets blocked by A-share lot rules in an unintended way
analysts produce empty, contradictory, or obviously wrong outputs
tool/data errors dominate the run
```

### Level 2: Short Trend Check

Purpose:

```text
Inspect trade frequency, cash usage, concentration, and obvious overfitting.
```

Window:

```text
around 10 trading days
```

Use this only after Level 1 passes.

Still do not treat this as final performance proof.

### Level 3: Full-Month Validation

Purpose:

```text
Evaluate performance after behavior is already acceptable.
```

Window:

```text
1 full month
```

Compare:

```text
total return
excess return vs benchmark
max drawdown
Sharpe
cash ratio
position concentration
industry concentration
trade count
whether performance comes from one lucky position
```

Only run this after Levels 0-2 pass or after we intentionally accept skipping
Level 2 for a narrow experiment.

## Change Ownership

Use the failure pattern to decide what to change:

```text
Data unstable, tool errors, fields messy
=> Fix data adapters and tools. Do not change strategy.

Analysts are empty, contradictory, miscalculate indicators, or overstate value
=> Fix analyst tools, prompts, schemas, or fixed indicators.

PM has useful signals but stays extremely defensive, or buys too aggressively
=> Fix PM rules and prompts.

Trade quantities, cash, A-share lots, T+1, or circuit breakers are wrong
=> Fix execution and PM hard constraints.
```

Do not change PM and analysts in the same experiment unless the experiment is
explicitly an integration test. Otherwise, we cannot attribute the result.

## PM Context Compression

PM cost and behavior are strongly affected by the dynamic context sent before
the decision call.

Observed issue:

```text
PM receives full analyst reports, risk text, tool traces, and conference text.
For an 8-stock pool, the PM section alone can exceed 180-220 KB per day.
```

Do not solve this by asking analysts to "summarize themselves" only. That can
drop inconvenient risks or preserve each analyst's bias. Do not rely on pure
regex extraction only either; analyst phrasing is unstable and conflicts can be
implicit.

Preferred experiment design:

```text
raw analyst text
=> structured facts extracted by the system
=> short excerpts for audit and conflict cases
=> PM receives compact facts, not full essays
```

Current switch:

```bash
PM_CONTEXT_MODE=compact
```

Compact mode should keep:

```text
signal / confidence / ticker
missing or timeout status
one short per-ticker excerpt per analyst
longer conflict excerpt only when bull/bear signals disagree
risk excerpt
raw byte stats for auditing compression ratio
```

Default mode remains `raw` until compact mode passes behavior smoke tests.

When testing compact mode, use a new config name. Compare:

```text
PM input/log size
number of timeout or malformed decisions
portfolio exposure
trade list
excess return
whether PM misses a risk that existed in the raw log
```

### Offline Replay Before LLM Rerun

Parser, schema, formatter, and compact-context fixes must be validated with
saved logs before running another LLM backtest.

Use:

```bash
python scripts/replay_pm_compact_context.py <config_name> \
  --start <YYYY-MM-DD> \
  --end <YYYY-MM-DD> \
  --tickers <ticker1,ticker2,...>
```

This script does not call any LLM. It replays existing `logs/daily/*_reasoning`
files and reports:

```text
old parser missing signal count
new parser missing signal count
compact context bytes
conflict tickers
per-ticker analyst signals
unstructured or failed agents
```

Only rerun a paid backtest when replay shows that:

```text
the compact facts are materially different
and the difference can change PM behavior
and the change cannot be judged from old logs alone
```

Current validated parser cases:

```text
SIGNAL: BULLISH / BEARISH aliases normalize to BULL / BEAR
SIGNAL: UP / DOWN aliases normalize to BULL / BEAR
Markdown signal tables are parsed, e.g. | 600519.SH | 公司 | BULL | 85 |
Normal phrases like "no room for error" are not treated as failed analysis
```

Example result from `backtest_202401_regime_pm_v4_weak_compact_smoke`:

```text
2024-01-02: missing signals old=8 new=0
2024-01-03: missing signals old=8 new=0
```

Conclusion:

```text
Do not rerun two trading days just to test this parser fix.
Use replay first; reserve LLM runs for behavior changes.
```

## Regime Evidence Before PM

Market-regime judgment is now split out of PM free-form reasoning.

System-generated evidence:

```text
regime_evidence.method = pool_internal_breadth_v1
regime_evidence.suggested_regime
regime_evidence.max_allowed_regime
regime_evidence.target_exposure_band
regime_evidence.technical_breadth
regime_evidence.sentiment_status
regime_evidence.clean_bullish_count
regime_evidence.conflict_count
regime_evidence.bearish_majority_count
regime_evidence.downgrade_reasons
```

PM rule:

```text
PM must not choose a regime above max_allowed_regime.
PM may choose a more defensive regime if portfolio/risk constraints justify it.
```

Offline replay validation:

```text
2024-01-02: suggested=WEAK, max=WEAK, target=40-60%
2024-01-03: suggested=WEAK, max=WEAK, target=40-60%

2024-02-20: suggested=REBOUND, max=REBOUND, target=80-90%
2024-02-21: suggested=REBOUND, max=REBOUND, target=80-90%
2024-02-22: suggested=SIDEWAYS_OR_SMALL_UP, max=SIDEWAYS_OR_SMALL_UP, target=65-75%
  Reason: sentiment_missing_or_sparse. Technical breadth was strong.
2024-02-23: suggested=REBOUND, max=REBOUND, target=80-90%
```

Important boundary:

```text
Portfolio concentration risk should affect sizing and trimming.
It should not by itself turn a technical rebound market into WEAK.
```

## Compact Analyst Output

Use this only after fixed indicators and parser replay pass:

```bash
ANALYST_OUTPUT_MODE=compact
```

This keeps analyst tool usage unchanged but compresses final responses:

```text
signal lines first
at most 2 short bullets per ticker
no broad market essays
no repeated full tool-result restatement
no long investment philosophy section
DATA_GAP lines for missing data
SUMMARY under 80 words
```

Default remains normal analyst output. Compact analyst output is a separate
experiment variable and should use a new `config_name`.

## Compact Reasoning Logs

Daily reasoning logs are for experiment review, not full raw trace archival.
Raw tool traces can dominate log size and hide the decision evidence.

Default:

```bash
REASONING_LOG_MODE=compact
```

Compact logs keep:

```text
PM final decisions and reasoning
tool_use_count / tool_result_count per agent
SIGNAL lines
DATA_GAP lines
SUMMARY lines
PM regime / exposure / blocker lines
short error snippets
```

Compact logs drop:

```text
full tool_result payloads
long thinking blocks
manual DSML/tool-call text residue
large raw K-line or financial table dumps
```

Use raw only when debugging a tool or model-format issue:

```bash
REASONING_LOG_MODE=raw
```

Important:

```text
Compact reasoning logs affect only newly generated logs.
Existing logs are not rewritten.
```

## Two-Stock Stage

The current two-stock stage is only for PM behavior calibration:

```text
600519.SH
601398.SH
```

Valid conclusions:

```text
PM v0 conservative behavior
PM small_probe behavior
PM lot_aware_probe behavior
A-share high-price lot handling
cash discipline
```

Invalid conclusions:

```text
long-term alpha
general strategy quality
large-stock-pool performance
market-regime robustness
```

One month is enough for this behavior-calibration stage. It is not enough to
prove the strategy.

## Expanded Pool Stage

After two-stock calibration, expand to a 6-8 stock watchlist and freeze the
current best PM. Do not change analysts at the same time.

Suggested first pool:

```text
600519.SH  Kweichow Moutai, high-price consumer
601398.SH  ICBC, low-price bank
000858.SZ  Wuliangye, consumer
000333.SZ  Midea, manufacturing
300750.SZ  CATL, high-price growth
600030.SH  CITIC Securities, broker
601318.SH  Ping An, insurance
600276.SH  Hengrui Medicine, healthcare
```

First run:

```text
8 stocks * 1 day
```

Then:

```text
8 stocks * 3-5 days
```

Only after those pass should we consider a full-month run.

## Market-Regime Stage

After the 8-stock pool behaves correctly, do not keep tuning on a single month.
Run short windows across different market regimes first.

Purpose:

```text
Check whether the PM rule is only good in one market environment.
```

Minimum regime set:

```text
Weak market: tests cash defense and drawdown control.
Rebound market: tests whether PM can raise exposure and avoid missing upside.
Sideways/small-up market: tests whether PM stays balanced without overtrading.
```

Design rule:

```text
Only change the time window / market regime.
Keep stock pool, analysts, model, data tools, indicators, and PM rules fixed.
```

Evaluate:

```text
agent return vs equal-weight benchmark
cash drag or cash defense
active stock PnL
final cash ratio
trade count
whether PM cut winners too early
whether PM stayed defensive when market improved
```

Example findings from the first 8-stock regime tests are recorded in:

```text
docs/pm_market_regime_findings.md
```

The key lesson:

```text
Do not call a PM rule "good" because it wins in a weak month.
Weak-market outperformance may come from cash defense rather than stock-picking.
If rebound and sideways windows show cash drag, add a market-regime target
exposure layer instead of blindly making the PM aggressive.
```

## Command Pattern

Prepare fixed experiment code first:

```bash
python scripts/prepare_experiment_code.py <config_name> \
  --start <YYYY-MM-DD> \
  --end <YYYY-MM-DD> \
  --date <YYYY-MM-DD> \
  --tickers <ticker1,ticker2,...> \
  --objective "<experiment goal>" \
  --variables "<unique variable and fixed conditions>"
```

Run backtest:

```bash
<EXPERIMENT_ENV_VARS> python run_backtest.py \
  --start <YYYY-MM-DD> \
  --end <YYYY-MM-DD> \
  --config-name <config_name> \
  --reset
```

Use a new `config_name` for every experiment group. Never overwrite baseline
results.

## Promotion Rule For LLM-Written Code

LLM-written code is not automatically promoted into fixed indicators just
because it ran once.

Promotion rule:

```text
experiment runs successfully
results look sane
candidate code is useful for future runs
=> promote candidate code into validated experiment indicators
```

Until then, candidate code remains a record, not a fixed research tool.
