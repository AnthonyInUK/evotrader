# -*- coding: utf-8 -*-
"""Validate dashboard JSON files consumed by the frontend."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED = {
    "summary.json": {
        "object": ["equity", "baseline", "baseline_vw", "momentum"],
    },
    "stats.json": {
        "object": [
            "totalAssetValue",
            "totalReturn",
            "cashPosition",
            "tickerWeights",
            "totalTrades",
        ],
    },
    "holdings.json": {"array": []},
    "trades.json": {"array": []},
    "leaderboard.json": {"array": []},
}


def _dashboard_dir(path: str) -> Path:
    candidate = Path(path)
    if candidate.name == "team_dashboard":
        return candidate
    return candidate / "team_dashboard"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(path: str) -> dict[str, Any]:
    dashboard_dir = _dashboard_dir(path)
    findings = []

    for name, spec in REQUIRED.items():
        file_path = dashboard_dir / name
        if not file_path.exists():
            findings.append({"file": name, "kind": "missing", "message": "not found"})
            continue
        try:
            data = _load(file_path)
        except json.JSONDecodeError as exc:
            findings.append(
                {"file": name, "kind": "json", "message": f"invalid JSON: {exc}"}
            )
            continue

        if "array" in spec and not isinstance(data, list):
            findings.append(
                {"file": name, "kind": "type", "message": "expected array"}
            )
            continue
        if "object" in spec:
            if not isinstance(data, dict):
                findings.append(
                    {"file": name, "kind": "type", "message": "expected object"}
                )
                continue
            for key in spec["object"]:
                if key not in data:
                    findings.append(
                        {
                            "file": name,
                            "kind": "field",
                            "message": f"missing key: {key}",
                        }
                    )

    return {
        "dashboardDir": str(dashboard_dir),
        "ok": not findings,
        "findings": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dashboard_or_config")
    parser.add_argument("--json-out")
    args = parser.parse_args()

    result = validate(args.dashboard_or_config)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(f"=== Dashboard Schema: {'PASS' if result['ok'] else 'WARN'} ===")
    print(f"Dashboard: {result['dashboardDir']}")
    for finding in result["findings"]:
        print(f"- {finding['file']} [{finding['kind']}] {finding['message']}")


if __name__ == "__main__":
    main()
