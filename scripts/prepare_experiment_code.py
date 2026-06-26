# -*- coding: utf-8 -*-
"""Prepare fixed, validated indicator code for a backtest experiment."""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

from backend.tools.analysis_tools import (  # noqa: E402
    _code_repair_cache_key,
    execute_code,
)
from backend.tools.data_tools import get_prices, prices_to_df  # noqa: E402
from backend.tools.analysis_tools import _add_basic_technical_indicators  # noqa: E402


DEFAULT_INDICATORS = [
    {
        "indicator_id": "technical_trend_snapshot_v1",
        "description": "Fixed trend snapshot: close, SMA20/SMA60, MACD, RSI14.",
        "lookback_days": 120,
        "code": """
summary = {}
for t, d in dfs.items():
    latest = d.iloc[-1]
    close = float(latest["close"])
    sma20 = float(latest["SMA20"])
    sma60 = float(latest["SMA60"])
    macd = float(latest["MACD"])
    signal = float(latest["MACD_signal"])
    rsi = float(latest["RSI14"])
    trend = "BULL" if close > sma20 > sma60 and macd > signal else "BEAR" if close < sma20 < sma60 and macd < signal else "NEUTRAL"
    summary[t] = {
        "close": round(close, 2),
        "sma20": round(sma20, 2),
        "sma60": round(sma60, 2),
        "macd": round(macd, 4),
        "macd_signal": round(signal, 4),
        "rsi14": round(rsi, 2),
        "trend": trend,
    }
result = summary
""".strip(),
    },
    {
        "indicator_id": "technical_momentum_risk_v1",
        "description": "Fixed momentum/risk snapshot: 5d/10d/20d returns and 20d volatility.",
        "lookback_days": 120,
        "code": """
summary = {}
for t, d in dfs.items():
    close = d["close"].dropna()
    returns = close.pct_change()
    def pct_change(days):
        if len(close) <= days:
            return None
        return round((close.iloc[-1] / close.iloc[-days - 1] - 1) * 100, 2)
    vol20 = returns.tail(20).std() * np.sqrt(252) * 100
    summary[t] = {
        "return_5d_pct": pct_change(5),
        "return_10d_pct": pct_change(10),
        "return_20d_pct": pct_change(20),
        "volatility_20d_pct": round(float(vol20), 2),
    }
result = summary
""".strip(),
    },
]


def _text(result) -> str:
    content = result.content[0]
    if hasattr(content, "text"):
        return content.text
    if isinstance(content, dict):
        return content.get("text", "")
    return str(content)


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


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _build_df_signature(tickers: list[str], date: str, lookback_days: int):
    dfs = {}
    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date="2023-01-01",
            end_date=date,
        )
        if prices:
            dfs[ticker] = _add_basic_technical_indicators(prices_to_df(prices))
    if not dfs:
        return None
    first_df = next(iter(dfs.values()))
    return first_df


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config_name")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--objective", default=None)
    parser.add_argument("--variables", default=None)
    parser.add_argument("--tickers", default=os.getenv("TICKERS", "600519.SH,601398.SH"))
    parser.add_argument("--ticker", default="600519.SH")
    parser.add_argument("--date", default="2024-01-17")
    parser.add_argument(
        "--source-cache",
        default=os.getenv(
            "CODE_REPAIR_CACHE_PATH",
            "backend/data/code_repair_cache/execute_code_repairs.jsonl",
        ),
    )
    args = parser.parse_args()

    config_dir = Path(args.config_name)
    tickers = [ticker.strip() for ticker in args.tickers.split(",") if ticker.strip()]
    experiment_path = (
        config_dir / "experiment_code" / "execute_code_validated.jsonl"
    )
    spec_path = config_dir / "experiment_code" / "experiment_spec.json"
    experiment_path.parent.mkdir(parents=True, exist_ok=True)

    spec = {
        "experiment_id": args.config_name,
        "objective": args.objective
        or "Evaluate fixed-indicator A-share multi-agent backtest behavior.",
        "period": {"start": args.start, "end": args.end},
        "variables": {
            "tickers": tickers,
            "model_provider": os.getenv("MODEL_PROVIDER"),
            "model_name": os.getenv("MODEL_NAME"),
            "max_comm_cycles": os.getenv("MAX_COMM_CYCLES"),
            "custom": args.variables,
        },
        "fixed_indicators": [
            {
                "indicator_id": item["indicator_id"],
                "description": item["description"],
                "lookback_days": item["lookback_days"],
            }
            for item in DEFAULT_INDICATORS
        ],
    }
    _write_json(spec_path, spec)

    source_cache = Path(args.source_cache)
    existing_keys = {
        record.get("cache_key")
        for record in _iter_jsonl(experiment_path) or []
        if record.get("cache_key")
    }

    validated = 0
    default_validated = 0
    skipped = 0
    failed = 0

    old_env = {
        "ENABLE_LLM_CODE_REPAIR": os.environ.get("ENABLE_LLM_CODE_REPAIR"),
        "ENABLE_CODE_REPAIR_CACHE": os.environ.get("ENABLE_CODE_REPAIR_CACHE"),
        "ENABLE_EXPERIMENT_CODE_CACHE": os.environ.get(
            "ENABLE_EXPERIMENT_CODE_CACHE"
        ),
    }
    os.environ["ENABLE_LLM_CODE_REPAIR"] = "0"
    os.environ["ENABLE_CODE_REPAIR_CACHE"] = "0"
    os.environ["ENABLE_EXPERIMENT_CODE_CACHE"] = "0"

    try:
        signature_df = _build_df_signature(tickers, args.date, 120)
        for item in DEFAULT_INDICATORS:
            code = item["code"]
            if signature_df is None:
                failed += 1
                continue
            cache_key = _code_repair_cache_key(code, signature_df)
            if cache_key in existing_keys:
                skipped += 1
                continue
            result = execute_code(
                tickers=tickers,
                current_date=args.date,
                code=code,
                lookback_days=item["lookback_days"],
            )
            text = _text(result)
            if "CODE_ERROR" in text or "[ERROR]" in text:
                failed += 1
                continue
            materialized = {
                "cache_key": cache_key,
                "success": True,
                "indicator_id": item["indicator_id"],
                "description": item["description"],
                "lookback_days": item["lookback_days"],
                "columns": "",
                "original_code": code,
                "repaired_code": code,
                "validated_for_config": args.config_name,
                "validation_ticker": ",".join(tickers),
                "validation_date": args.date,
            }
            with experiment_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(materialized, ensure_ascii=False) + "\n")
            existing_keys.add(cache_key)
            validated += 1
            default_validated += 1

        for record in _iter_jsonl(source_cache) or []:
            if not record.get("success"):
                skipped += 1
                continue
            cache_key = record.get("cache_key")
            repaired_code = record.get("repaired_code", "")
            if not cache_key or not repaired_code or cache_key in existing_keys:
                skipped += 1
                continue

            result = execute_code(
                tickers=[args.ticker],
                current_date=args.date,
                code=repaired_code,
                lookback_days=120,
            )
            text = _text(result)
            if "CODE_ERROR" in text or "[ERROR]" in text:
                failed += 1
                continue

            materialized = {
                **record,
                "validated_for_config": args.config_name,
                "validation_ticker": args.ticker,
                "validation_date": args.date,
            }
            with experiment_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(materialized, ensure_ascii=False) + "\n")
            existing_keys.add(cache_key)
            validated += 1
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print("=== Experiment Code Preparation ===")
    print(f"Config: {args.config_name}")
    print(f"Source cache: {source_cache}")
    print(f"Experiment spec: {spec_path}")
    print(f"Experiment code: {experiment_path}")
    print(f"Default indicators validated: {default_validated}")
    print(f"Validated: {validated}")
    print(f"Skipped: {skipped}")
    print(f"Failed validation: {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
