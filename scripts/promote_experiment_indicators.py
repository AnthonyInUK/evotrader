# -*- coding: utf-8 -*-
"""
Promote successful experiment candidate code into fixed run_indicator scripts.

Use this after an experiment segment has run and you have accepted the candidate
code/results. Promotion writes records to:
  <config>/experiment_code/execute_code_validated.jsonl
"""
import argparse
import json
import re
from pathlib import Path


def _iter_jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip().lower()).strip("_")
    return slug or "indicator"


def _existing_indicator_ids(path: Path) -> set[str]:
    return {
        record.get("indicator_id")
        for record in _iter_jsonl(path) or []
        if record.get("indicator_id")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_name")
    parser.add_argument(
        "--candidate-id",
        action="append",
        default=[],
        help="Candidate ID to promote. Can be passed multiple times.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Promote all candidates in this experiment.",
    )
    parser.add_argument(
        "--indicator-prefix",
        default="experiment_indicator",
        help="Prefix for generated indicator IDs.",
    )
    parser.add_argument(
        "--description",
        default="Promoted candidate indicator from a completed experiment.",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_name)
    candidate_path = config_dir / "experiment_code" / "candidate_indicators.jsonl"
    validated_path = config_dir / "experiment_code" / "execute_code_validated.jsonl"
    validated_path.parent.mkdir(parents=True, exist_ok=True)

    candidates = list(_iter_jsonl(candidate_path) or [])
    if not candidates:
        print(f"No candidates found: {candidate_path}")
        return 1

    selected_ids = set(args.candidate_id)
    if not args.all and not selected_ids:
        print("Pass --all or at least one --candidate-id.")
        print("Available candidates:")
        for record in candidates:
            print(f"- {record.get('candidate_id')} source={record.get('source')}")
        return 1

    existing_ids = _existing_indicator_ids(validated_path)
    promoted = 0
    skipped = 0
    with validated_path.open("a", encoding="utf-8") as handle:
        for record in candidates:
            candidate_id = record.get("candidate_id")
            if not args.all and candidate_id not in selected_ids:
                skipped += 1
                continue

            suffix = candidate_id or record.get("cache_key", "")[:12]
            indicator_id = f"{_slug(args.indicator_prefix)}_{_slug(suffix)}"
            if indicator_id in existing_ids:
                skipped += 1
                continue

            promoted_record = {
                "cache_key": record.get("cache_key"),
                "success": True,
                "indicator_id": indicator_id,
                "description": args.description,
                "lookback_days": int(record.get("lookback_days") or 120),
                "columns": record.get("columns", ""),
                "original_code": record.get("original_code", ""),
                "repaired_code": record.get("executable_code")
                or record.get("repaired_code")
                or record.get("original_code", ""),
                "promoted_from_candidate_id": candidate_id,
                "validated_for_config": args.config_name,
                "result_preview": record.get("result_preview", ""),
            }
            handle.write(json.dumps(promoted_record, ensure_ascii=False) + "\n")
            existing_ids.add(indicator_id)
            promoted += 1

    print("=== Promote Experiment Indicators ===")
    print(f"Config: {args.config_name}")
    print(f"Candidates: {candidate_path}")
    print(f"Validated indicators: {validated_path}")
    print(f"Promoted: {promoted}")
    print(f"Skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
