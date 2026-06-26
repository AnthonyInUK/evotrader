#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


OUT_DIR = ROOT / "outputs" / "experiments"
DOCS_DIR = ROOT / "docs"


def build_registry() -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = [
        {
            "name": "memory_ab_q1",
            "type": "agent_memory_ab",
            "window": "2025 Q1 aligned 33 trading days",
            "uses_llm": True,
            "sample_size": "33 trading days",
            "metrics": {
                "relative_win_rate_t0_pp": 5.4,
                "ic_t1_improvement": 0.0213,
            },
            "conclusion": "Long-term memory improved short-horizon decision quality in the aligned Q1 window.",
            "boundary": "Use aligned trading days only; do not describe as a full calendar-quarter benchmark.",
            "artifacts": [],
        },
    ]
    registry.extend(_selection_attribution_entries())
    registry.extend(_execution_ablation_entries())
    registry.extend(_selector_robustness_entries())
    return registry


def _selection_attribution_entries() -> list[dict[str, Any]]:
    entries = []
    for path in sorted((OUT_DIR / "selection_attribution").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        selector = data["selector_aggregate"]
        baseline = data["fixed_baseline_aggregate"]
        entries.append(
            {
                "name": f"selection_attribution_{data['start_date']}_{data['end_date']}",
                "type": "selector_attribution",
                "window": f"{data['start_date']} to {data['end_date']}",
                "uses_llm": False,
                "sample_size": {
                    "trading_days": data["trading_days"],
                    "selector_count": selector["count"],
                    "baseline_count": baseline["count"],
                },
                "metrics": {
                    "selector_t5_avg_return": selector["avg_forward_return_5d"],
                    "selector_t5_win_rate": selector["win_rate_5d"],
                    "baseline_t5_avg_return": baseline["avg_forward_return_5d"],
                    "baseline_t5_win_rate": baseline["win_rate_5d"],
                },
                "conclusion": "Selector attribution measures candidate-pool short-horizon quality against the YAML fixed-universe baseline.",
                "boundary": "Attribution only; not a live trading PnL claim. Theme membership is degraded when baostock is unavailable.",
                "artifacts": [str(path.relative_to(ROOT))],
            },
        )
    return entries


def _execution_ablation_entries() -> list[dict[str, Any]]:
    entries = []
    for path in sorted((OUT_DIR / "execution_ablation").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        agg = data["aggregate"]
        entries.append(
            {
                "name": f"execution_ablation_{data['start_date']}_{data['end_date']}",
                "type": "risk_execution_ablation",
                "window": f"{data['start_date']} to {data['end_date']}",
                "uses_llm": False,
                "sample_size": {
                    "trading_days": data["trading_days"],
                    "raw_orders": agg["raw_orders"],
                },
                "metrics": {
                    "risk_adjusted": agg["risk_adjusted"],
                    "execution_adjusted": agg["execution_adjusted"],
                    "blocked": agg["blocked"],
                    "total_transaction_cost": agg["total_transaction_cost"],
                },
                "conclusion": "RiskGuard and A-share execution constraints convert paper decisions into executable orders.",
                "boundary": "Uses selector-implied buy intents, not fresh LLM PM decisions.",
                "artifacts": [str(path.relative_to(ROOT))],
            },
        )
    return entries


def _selector_robustness_entries() -> list[dict[str, Any]]:
    entries = []
    for path in sorted((OUT_DIR / "selector_robustness").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        entries.append(
            {
                "name": "selector_robustness_multi_regime",
                "type": "selector_robustness",
                "window": ", ".join(
                    f"{name}: {item['start_date']} to {item['end_date']}"
                    for name, item in data["windows"].items()
                ),
                "uses_llm": False,
                "sample_size": {
                    name: {
                        "trading_days": item["trading_days"],
                        "all_top8_count": item["variants"]["all_top8"]["count"],
                    }
                    for name, item in data["windows"].items()
                },
                "metrics": {
                    name: {
                        "all_top8_t5": item["variants"]["all_top8"]["avg_forward_return_5d"],
                        "technical_only_t5": item["variants"]["technical_only_top8"]["avg_forward_return_5d"],
                    }
                    for name, item in data["windows"].items()
                },
                "conclusion": "Selector performance is regime-sensitive; rebound window is positive while weak/sideways windows are negative.",
                "boundary": "Random baseline was skipped in the lightweight run; results are based on local cache coverage.",
                "artifacts": [str(path.relative_to(ROOT))],
            },
        )
    return entries


def write_outputs(registry: list[dict[str, Any]]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = OUT_DIR / "experiment_registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# EvoTraders Experiment Summary",
        "",
        "This file is generated from local experiment artifacts. It records what each experiment proves and what it does not prove.",
        "",
    ]
    for item in registry:
        lines.extend(
            [
                f"## {item['name']}",
                "",
                f"- Type: `{item['type']}`",
                f"- Window: {item['window']}",
                f"- Uses LLM: {item['uses_llm']}",
                f"- Sample size: `{json.dumps(item['sample_size'], ensure_ascii=False)}`",
                f"- Metrics: `{json.dumps(item['metrics'], ensure_ascii=False)}`",
                f"- Conclusion: {item['conclusion']}",
                f"- Boundary: {item['boundary']}",
                f"- Artifacts: {', '.join(item['artifacts']) if item['artifacts'] else 'manual project records'}",
                "",
            ],
        )
    (DOCS_DIR / "experiment_summary.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    registry = build_registry()
    write_outputs(registry)
    print(json.dumps({"experiments": len(registry)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
