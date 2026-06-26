# EvoTraders Experiment Summary

This file is generated from local experiment artifacts. It records what each experiment proves and what it does not prove.

## memory_ab_q1

- Type: `agent_memory_ab`
- Window: 2025 Q1 aligned 33 trading days
- Uses LLM: True
- Sample size: `"33 trading days"`
- Metrics: `{"relative_win_rate_t0_pp": 5.4, "ic_t1_improvement": 0.0213}`
- Conclusion: Long-term memory improved short-horizon decision quality in the aligned Q1 window.
- Boundary: Use aligned trading days only; do not describe as a full calendar-quarter benchmark.
- Artifacts: manual project records

## selection_attribution_2024-02-20_2024-02-26

- Type: `selector_attribution`
- Window: 2024-02-20 to 2024-02-26
- Uses LLM: False
- Sample size: `{"trading_days": 5, "selector_count": 40, "baseline_count": 25}`
- Metrics: `{"selector_t5_avg_return": 0.015251, "selector_t5_win_rate": 0.4, "baseline_t5_avg_return": 0.004782, "baseline_t5_win_rate": 0.56}`
- Conclusion: Selector attribution measures candidate-pool short-horizon quality against the YAML fixed-universe baseline.
- Boundary: Attribution only; not a live trading PnL claim. Theme membership is degraded when baostock is unavailable.
- Artifacts: outputs/experiments/selection_attribution/momentum_v1_20240220_20240226.json

## selection_attribution_2024-02-20_2024-03-18

- Type: `selector_attribution`
- Window: 2024-02-20 to 2024-03-18
- Uses LLM: False
- Sample size: `{"trading_days": 20, "selector_count": 160, "baseline_count": 100}`
- Metrics: `{"selector_t5_avg_return": 0.029666, "selector_t5_win_rate": 0.525, "baseline_t5_avg_return": 0.010301, "baseline_t5_win_rate": 0.49}`
- Conclusion: Selector attribution measures candidate-pool short-horizon quality against the YAML fixed-universe baseline.
- Boundary: Attribution only; not a live trading PnL claim. Theme membership is degraded when baostock is unavailable.
- Artifacts: outputs/experiments/selection_attribution/momentum_v1_20240220_20240318.json

## execution_ablation_2024-02-20_2024-02-26

- Type: `risk_execution_ablation`
- Window: 2024-02-20 to 2024-02-26
- Uses LLM: False
- Sample size: `{"trading_days": 5, "raw_orders": 40}`
- Metrics: `{"risk_adjusted": 40, "execution_adjusted": 40, "blocked": 5, "total_transaction_cost": 1639.19}`
- Conclusion: RiskGuard and A-share execution constraints convert paper decisions into executable orders.
- Boundary: Uses selector-implied buy intents, not fresh LLM PM decisions.
- Artifacts: outputs/experiments/execution_ablation/momentum_v1_20240220_20240226.json

## selector_robustness_multi_regime

- Type: `selector_robustness`
- Window: weak_202401: 2024-01-02 to 2024-01-15, rebound_202402: 2024-02-20 to 2024-03-04, sideways_202405: 2024-05-06 to 2024-05-17
- Uses LLM: False
- Sample size: `{"weak_202401": {"trading_days": 10, "all_top8_count": 80}, "rebound_202402": {"trading_days": 10, "all_top8_count": 80}, "sideways_202405": {"trading_days": 10, "all_top8_count": 80}}`
- Metrics: `{"weak_202401": {"all_top8_t5": -0.015514, "technical_only_t5": -0.014117}, "rebound_202402": {"all_top8_t5": 0.018132, "technical_only_t5": 0.019208}, "sideways_202405": {"all_top8_t5": -0.031816, "technical_only_t5": -0.024998}}`
- Conclusion: Selector performance is regime-sensitive; rebound window is positive while weak/sideways windows are negative.
- Boundary: Random baseline was skipped in the lightweight run; results are based on local cache coverage.
- Artifacts: outputs/experiments/selector_robustness/momentum_v1_selector_robustness.json
