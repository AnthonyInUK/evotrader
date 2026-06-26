# -*- coding: utf-8 -*-
"""
Analysis tools for fundamental, technical, sentiment, and valuation analysis.

All tools accept tickers as List[str] with default from analysis context.
Returns human-readable text format for easy LLM consumption.
"""
# flake8: noqa: E501
# pylint: disable=C0301,W0613
import json
import logging
import math
import os
import re
import traceback
from datetime import datetime, timedelta, timezone
from functools import wraps
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any, List, Optional

import numpy as np
import pandas as pd
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from backend.tools.data_tools import (
    get_ashare_research_reports,
    get_ashare_top_holders,
    get_company_news,
    get_company_news_with_trace,
    get_financial_metrics,
    get_insider_trades,
    get_market_cap,
    get_prices,
    prices_to_df,
    search_line_items,
)

logger = logging.getLogger(__name__)

_A_SHARE_NAME_MAP = {
    "贵州茅台": "600519.SH",
    "茅台": "600519.SH",
    "宁德时代": "300750.SZ",
    "平安银行": "000001.SZ",
}

_A_SHARE_CONCEPT_MAP = {
    "600519.SH": ["白酒", "消费", "高端品牌", "沪深300"],
    "300750.SZ": ["新能源", "锂电池", "储能", "创业板"],
    "000001.SZ": ["银行", "金融", "深市蓝筹"],
}


_NEWS_TRANSPORT_LABELS = {
    "memory_cache": "memory cache",
    "local_snapshot": "local snapshot",
    "live_fetch": "live fetch",
    "empty": "no data",
}

_OPENAI_COMPATIBLE_BASE_URLS = {
    "DEEPSEEK": "https://api.deepseek.com/v1",
    "GROQ": "https://api.groq.com/openai/v1",
    "OPENROUTER": "https://openrouter.ai/api/v1",
}

_OPENAI_COMPATIBLE_API_KEYS = {
    "OPENAI": "OPENAI_API_KEY",
    "DEEPSEEK": "DEEPSEEK_API_KEY",
    "GROQ": "GROQ_API_KEY",
    "OPENROUTER": "OPENROUTER_API_KEY",
}


def _to_text_response(text: str) -> ToolResponse:
    """Convert text string to ToolResponse."""
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _text_response_content(result: ToolResponse) -> str:
    content = result.content[0]
    if hasattr(content, "text"):
        return content.text
    if isinstance(content, dict):
        return content.get("text", "")
    return str(content)


def _safe_float(value, default=0.0) -> float:
    """Safely convert to float."""
    try:
        if pd.isna(value) or np.isnan(value):
            return default
        return float(value)
    except (ValueError, TypeError, OverflowError):
        return default


def safe(func):
    """Decorator to catch exceptions in tool functions."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            error_msg = f"Error in {func.__name__}: {str(e)}"
            logger.error(f"{error_msg}\n{traceback.format_exc()}")
            return _to_text_response(f"[ERROR] {error_msg}")

    return wrapper


def _fmt(val, fmt=".2f", suffix="") -> str:
    """Format value with handling for None."""
    if val is None:
        return "N/A"
    try:
        return f"{val:{fmt}}{suffix}"
    except (ValueError, TypeError):
        return str(val)


def _resolved_date(current_date: Optional[str]) -> str:
    """
    Ensure we always return a concrete YYYY-MM-DD date string.

    Day 10-11 requires A-share date compatibility. finance-mcp style tools often
    accept YYYYMMDD, while EvoTraders internals mostly use YYYY-MM-DD.
    """
    if not current_date:
        return datetime.today().strftime("%Y-%m-%d")

    raw = str(current_date).strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def _normalize_ticker_or_name(value: str) -> str:
    """Normalize A-share names/codes to internal ticker format."""
    raw = (value or "").strip()
    if not raw:
        return raw

    if raw in _A_SHARE_NAME_MAP:
        return _A_SHARE_NAME_MAP[raw]

    upper = raw.upper()
    if upper.endswith((".SH", ".SZ", ".BJ")):
        return upper

    if raw.isdigit() and len(raw) == 6:
        if raw.startswith("6"):
            return f"{raw}.SH"
        if raw.startswith(("0", "3")):
            return f"{raw}.SZ"
        return raw

    return raw


def _normalize_tickers(tickers: Optional[List[str]]) -> List[str]:
    return [_normalize_ticker_or_name(t) for t in (tickers or []) if t]


def _portfolio_from_payload(portfolio: Optional[dict]) -> dict:
    return portfolio or {"cash": 0.0, "positions": {}, "margin_used": 0.0}


def _add_basic_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for alias, source in {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }.items():
        if alias not in df.columns and source in df.columns:
            df[alias] = df[source]
    if "date" not in df.columns:
        if "time" in df.columns:
            df["date"] = df["time"]
        else:
            df["date"] = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    if "time" not in df.columns:
        df["time"] = df["date"]
    df["SMA20"] = df["close"].rolling(window=min(20, len(df))).mean()
    df["SMA50"] = df["close"].rolling(window=min(50, len(df))).mean()
    df["SMA60"] = df["close"].rolling(window=min(60, len(df))).mean()
    df["EMA12"] = df["close"].ewm(span=min(12, max(3, len(df) // 3))).mean()
    df["EMA26"] = df["close"].ewm(span=min(26, max(6, len(df) // 2))).mean()
    df["MACD"] = df["EMA12"] - df["EMA26"]
    df["MACD_signal"] = df["MACD"].ewm(span=9).mean()

    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI14"] = 100 - (100 / (1 + rs))
    df["returns"] = df["close"].pct_change()
    return df


def _format_execute_columns(df: pd.DataFrame) -> str:
    columns = df.columns
    if isinstance(columns, pd.MultiIndex):
        rendered = ["/".join(str(part) for part in col) for col in columns[:20]]
    else:
        rendered = [str(col) for col in columns[:30]]
    suffix = " ..." if len(columns) > len(rendered) else ""
    return ", ".join(rendered) + suffix


def _code_repair_cache_path() -> Path:
    return Path(
        os.getenv(
            "CODE_REPAIR_CACHE_PATH",
            "backend/data/code_repair_cache/execute_code_repairs.jsonl",
        )
    )


def _experiment_code_path() -> Optional[Path]:
    explicit = os.getenv("EXPERIMENT_CODE_PATH")
    if explicit:
        return Path(explicit)

    config_name = os.getenv("EVOTRADERS_CONFIG_NAME")
    if not config_name:
        return None
    return Path(config_name) / "experiment_code" / "execute_code_validated.jsonl"


def _candidate_indicator_path() -> Optional[Path]:
    explicit = os.getenv("CANDIDATE_INDICATOR_PATH")
    if explicit:
        return Path(explicit)

    config_name = os.getenv("EVOTRADERS_CONFIG_NAME")
    if not config_name:
        return None
    return Path(config_name) / "experiment_code" / "candidate_indicators.jsonl"


def _code_repair_cache_key(code: str, df: pd.DataFrame) -> str:
    payload = json.dumps(
        {
            "code": code.strip(),
            "columns": _format_execute_columns(df),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _load_repaired_code_from_path(
    path: Optional[Path],
    code: str,
    df: pd.DataFrame,
) -> Optional[str]:
    if path is None:
        return None
    if not path.exists():
        return None

    key = _code_repair_cache_key(code, df)
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("cache_key") == key and record.get("success"):
                return record.get("repaired_code")
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Code repair cache read failed: %s", exc)
    return None


def _load_experiment_validated_code(code: str, df: pd.DataFrame) -> Optional[str]:
    if not _env_flag("ENABLE_EXPERIMENT_CODE_CACHE", True):
        return None
    return _load_repaired_code_from_path(_experiment_code_path(), code, df)


def _load_experiment_indicator(indicator_id: str) -> Optional[dict]:
    if not _env_flag("ENABLE_EXPERIMENT_CODE_CACHE", True):
        return None
    path = _experiment_code_path()
    if path is None or not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("indicator_id") == indicator_id and record.get("success"):
                return record
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Experiment indicator read failed: %s", exc)
    return None


def _list_experiment_indicators() -> List[str]:
    path = _experiment_code_path()
    if path is None or not path.exists():
        return []
    indicator_ids = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            indicator_id = record.get("indicator_id")
            if indicator_id and record.get("success"):
                indicator_ids.append(indicator_id)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Experiment indicator list failed: %s", exc)
    return sorted(set(indicator_ids))


def _load_cached_repaired_code(code: str, df: pd.DataFrame) -> Optional[str]:
    if not _env_flag("ENABLE_CODE_REPAIR_CACHE", True):
        return None
    return _load_repaired_code_from_path(_code_repair_cache_path(), code, df)


def _save_repaired_code_record(
    path: Optional[Path],
    original_code: str,
    repaired_code: str,
    df: pd.DataFrame,
    success: bool,
    error: str,
) -> None:
    if path is None:
        return
    if not repaired_code.strip():
        return

    record = {
        "cache_key": _code_repair_cache_key(original_code, df),
        "success": success,
        "error": error,
        "columns": _format_execute_columns(df),
        "original_code": original_code.strip(),
        "repaired_code": repaired_code.strip(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Code repair cache write failed: %s", exc)


def _save_cached_repaired_code(
    original_code: str,
    repaired_code: str,
    df: pd.DataFrame,
    success: bool,
    error: str,
) -> None:
    if _env_flag("ENABLE_CODE_REPAIR_CACHE", True):
        _save_repaired_code_record(
            _code_repair_cache_path(),
            original_code,
            repaired_code,
            df,
            success,
            error,
        )
    if _env_flag("ENABLE_EXPERIMENT_CODE_CACHE", True):
        _save_repaired_code_record(
            _experiment_code_path(),
            original_code,
            repaired_code,
            df,
            success,
            error,
        )


def _save_candidate_indicator_code(
    original_code: str,
    executable_code: str,
    df: pd.DataFrame,
    ticker: str,
    current_date: str,
    lookback_days: int,
    result: object,
    source: str,
) -> None:
    if not _env_flag("ENABLE_CANDIDATE_INDICATOR_CAPTURE", True):
        return

    path = _candidate_indicator_path()
    if path is None:
        return

    cache_key = _code_repair_cache_key(original_code, df)
    existing_keys = set()
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                existing = json.loads(line)
                if existing.get("cache_key"):
                    existing_keys.add(existing["cache_key"])
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Candidate indicator cache read failed: %s", exc)

    if cache_key in existing_keys:
        return

    if isinstance(result, pd.DataFrame):
        result_preview = result.tail(3).to_string()
    elif isinstance(result, pd.Series):
        result_preview = result.tail(3).to_string()
    else:
        result_preview = str(result)[:2000]

    record = {
        "cache_key": cache_key,
        "candidate_id": f"candidate_{cache_key[:12]}",
        "source": source,
        "ticker": ticker,
        "current_date": current_date,
        "lookback_days": lookback_days,
        "columns": _format_execute_columns(df),
        "original_code": original_code.strip(),
        "executable_code": executable_code.strip(),
        "result_preview": result_preview,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "candidate",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("Candidate indicator cache write failed: %s", exc)


def _extract_name_error_name(exc: Exception) -> Optional[str]:
    match = re.search(r"name '([^']+)' is not defined", str(exc))
    return match.group(1) if match else None


def _extract_key_error_name(exc: Exception) -> Optional[str]:
    if not isinstance(exc, KeyError) or not exc.args:
        return None
    key = exc.args[0]
    return key if isinstance(key, str) else None


def _repair_dataframe_column_alias(df: pd.DataFrame, missing: str) -> bool:
    def _norm(value: object) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    target = _norm(missing)
    if isinstance(df.columns, pd.MultiIndex):
        repaired = False
        for ticker_level in df.columns.get_level_values(0).unique():
            level_df = df[ticker_level]
            matches = [col for col in level_df.columns if _norm(col) == target]
            if matches and (ticker_level, missing) not in df.columns:
                df[(ticker_level, missing)] = level_df[matches[0]]
                repaired = True
        return repaired

    matches = [col for col in df.columns if _norm(col) == target]
    if matches and missing not in df.columns:
        df[missing] = df[matches[0]]
        return True
    return False


def _format_execute_code_failure(
    ticker: str,
    current_date: str,
    code: str,
    exc: Exception,
    df: pd.DataFrame,
) -> str:
    exec_line = "unknown"
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == "<string>":
            exec_line = str(frame.lineno)

    preview = df.tail(3).to_string()
    return (
        f"=== execute_code ({ticker}, {current_date}) ===\n"
        "CODE_ERROR: custom indicator code failed after sandbox self-check.\n"
        f"Error: {type(exc).__name__}: {exc}\n"
        f"Code line: {exec_line}\n"
        f"Available columns: {_format_execute_columns(df)}\n\n"
        "Recent data preview:\n"
        f"{preview}\n\n"
        "Guidance: produce a conservative NEUTRAL technical signal if this "
        "custom calculation is not essential."
    )


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    fence = re.search(r"```(?:python)?\s*(.*?)```", stripped, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return stripped


def _repair_code_with_llm(
    code: str,
    exc: Exception,
    df: pd.DataFrame,
    ticker: str,
    current_date: str,
) -> Optional[str]:
    if not _env_flag("ENABLE_LLM_CODE_REPAIR", False):
        return None

    provider = os.getenv("CODE_REPAIR_MODEL_PROVIDER") or os.getenv(
        "MODEL_PROVIDER", "OPENAI"
    )
    provider = provider.upper()
    model_name = os.getenv("CODE_REPAIR_MODEL_NAME") or os.getenv(
        "MODEL_NAME", "gpt-4o-mini"
    )
    api_key_name = _OPENAI_COMPATIBLE_API_KEYS.get(provider)
    api_key = os.getenv(api_key_name or "")
    if not api_key:
        logger.warning(
            "LLM code repair skipped: missing API key for provider %s", provider
        )
        return None

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("LLM code repair skipped: openai package unavailable")
        return None

    client_args = {"api_key": api_key}
    base_url = (
        os.getenv("CODE_REPAIR_BASE_URL")
        or os.getenv(f"{provider}_BASE_URL")
        or _OPENAI_COMPATIBLE_BASE_URLS.get(provider)
    )
    if base_url:
        client_args["base_url"] = base_url

    prompt_payload = {
        "ticker": ticker,
        "current_date": current_date,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "available_columns": _format_execute_columns(df),
        "recent_data_preview": df.tail(3).to_string(),
        "code": code,
    }
    system_prompt = (
        "You repair Python pandas code for a read-only trading indicator sandbox. "
        "Return only corrected Python code. Do not explain. Do not import unsafe "
        "modules. Preserve the variable `result` as the final output. Available "
        "objects: df, dfs, tickers, ticker, current_date, pd, np, math."
    )

    try:
        client = OpenAI(**client_args)
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(prompt_payload, ensure_ascii=False),
                },
            ],
            temperature=0,
            timeout=_env_int("CODE_REPAIR_TIMEOUT_SECONDS", 30),
        )
    except Exception as repair_exc:  # noqa: BLE001
        logger.warning("LLM code repair failed: %s", repair_exc)
        return None

    repaired = response.choices[0].message.content if response.choices else ""
    repaired = _strip_code_fence(repaired or "")
    return repaired or None


def _render_news_trace(trace: dict) -> str:
    source = trace.get("source", "unknown")
    transport = _NEWS_TRANSPORT_LABELS.get(
        trace.get("transport", "unknown"),
        trace.get("transport", "unknown"),
    )
    return f"[News Evidence] source={source}, transport={transport}"


@safe
def extract_entities_code(
    text: str,
) -> ToolResponse:
    """
    从中文股票名或 6 位代码解析成 EvoTraders 内部 ticker。

    这是 Day 8-11 的薄封装版本：
    - 先支持常见 A 股名 / 代码输入
    - 输出标准 ticker，供后续财务/技术工具直接复用
    """
    candidates = [chunk.strip() for chunk in text.replace("，", ",").split(",")]
    resolved = []
    for candidate in candidates:
        if not candidate:
            continue
        resolved.append(f"{candidate} -> {_normalize_ticker_or_name(candidate)}")

    if not resolved:
        return _to_text_response("No ticker/entity detected.")

    return _to_text_response("=== Entity Code Resolution ===\n" + "\n".join(resolved))


@safe
def crawl_ths_finance(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    A股财务数据入口工具（薄封装版）。

    业务上对应第二周的“先取真实财务数据，再做基本面分析”。
    当前实现复用现有 financial datasets / finnhub 数据通道，
    但把输出组织成更接近 finance-mcp 的原始财务快照。
    """
    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    lines = [f"=== Finance Snapshot ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: No financial data available\n")
            continue

        m = metrics[0]
        lines.append(f"{ticker}:")
        lines.append(f"  Revenue Growth: {_fmt(m.revenue_growth, '.1%')}")
        lines.append(f"  Earnings Growth: {_fmt(m.earnings_growth, '.1%')}")
        lines.append(f"  ROE: {_fmt(m.return_on_equity, '.1%')}")
        lines.append(f"  Net Margin: {_fmt(m.net_margin, '.1%')}")
        lines.append(f"  Current Ratio: {_fmt(m.current_ratio)}")
        lines.append(f"  Debt to Equity: {_fmt(m.debt_to_equity)}")
        lines.append(f"  P/E: {_fmt(m.price_to_earnings_ratio)}")
        lines.append(f"  P/B: {_fmt(m.price_to_book_ratio)}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def history_calculate(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
    lookback_days: int = 60,
) -> ToolResponse:
    """
    A股历史行情 + 技术指标入口工具（薄封装版）。

    返回 PM/技术分析师最常用的一组指标：
    - close / SMA20 / SMA60
    - MACD / signal
    - RSI14
    - 20日波动率
    """
    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=max(lookback_days, 30))).strftime("%Y-%m-%d")
    lines = [f"=== Historical Indicator Snapshot ({current_date}) ===\n"]

    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date=start_date,
            end_date=current_date,
        )
        if not prices or len(prices) < 10:
            lines.append(f"{ticker}: Insufficient price data\n")
            continue

        df = _add_basic_technical_indicators(prices_to_df(prices))

        latest = df.iloc[-1]
        volatility_20 = _safe_float(df["returns"].tail(min(20, len(df))).std() * np.sqrt(252) * 100)

        lines.append(f"{ticker}:")
        lines.append(f"  Close: {_fmt(latest['close'])}")
        lines.append(f"  SMA20: {_fmt(latest['SMA20'])}")
        lines.append(f"  SMA60: {_fmt(latest['SMA60'])}")
        lines.append(f"  MACD: {_fmt(latest['MACD'], '.3f')}")
        lines.append(f"  MACD Signal: {_fmt(latest['MACD_signal'], '.3f')}")
        lines.append(f"  RSI14: {_fmt(latest['RSI14'], '.1f')}")
        lines.append(f"  Volatility20: {_fmt(volatility_20, '.1f', '%')}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def execute_code(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
    code: str = "",
    lookback_days: int = 120,
) -> ToolResponse:
    """
    Execute lightweight custom indicator code against historical price data.

    Contract:
    - Single ticker: loads historical OHLCV data into flat `df`
    - Multiple tickers: loads a ticker-keyed MultiIndex DataFrame into `df`
      and a `{ticker: flat_df}` mapping into `dfs`
    - User code can compute custom columns / signals
    - Final output should be assigned to `result`

    This gives the technical analyst a bounded “custom indicator sandbox”
    without requiring it to hallucinate calculations in plain text.
    """
    tickers = _normalize_tickers(tickers)
    current_date = _resolved_date(current_date)

    if not tickers:
        return _to_text_response("[ERROR] execute_code requires at least one ticker.")
    if not code.strip():
        return _to_text_response("[ERROR] execute_code requires non-empty code.")

    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=max(lookback_days, 30))).strftime("%Y-%m-%d")

    dfs = {}
    insufficient = []
    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date=start_date,
            end_date=current_date,
        )
        if not prices or len(prices) < 5:
            insufficient.append(ticker)
            continue
        ticker_df = _add_basic_technical_indicators(prices_to_df(prices))
        dfs[ticker] = ticker_df

    if not dfs:
        return _to_text_response(
            f"{', '.join(insufficient or tickers)}: Insufficient price data for execute_code"
        )

    ticker = next(iter(dfs))
    if len(dfs) == 1:
        df = dfs[ticker]
    else:
        df = pd.concat(dfs, axis=1)

    captured_stdout = []

    def safe_print(*args, sep=" ", end="\n"):
        captured_stdout.append(sep.join(str(arg) for arg in args) + end)

    safe_builtins = {
        "__import__": __import__,
        "abs": abs,
        "min": min,
        "max": max,
        "sum": sum,
        "len": len,
        "round": round,
        "float": float,
        "int": int,
        "str": str,
        "bool": bool,
        "range": range,
        "print": safe_print,
        "list": list,
        "dict": dict,
        "tuple": tuple,
        "set": set,
        "enumerate": enumerate,
        "zip": zip,
        "sorted": sorted,
        "any": any,
        "all": all,
        "isinstance": isinstance,
        "type": type,
        "reversed": reversed,
        "map": map,
        "filter": filter,
        "slice": slice,
    }

    def make_namespace():
        return {
            "__builtins__": safe_builtins,
            "pd": pd,
            "np": np,
            "math": math,
            "df": df,
            "dfs": dfs,
            "tickers": list(dfs.keys()),
            "ticker": ticker,
            "current_date": current_date,
            "result": None,
        }

    exec_namespace = make_namespace()
    executable_code = code
    cached_repaired_code = _load_experiment_validated_code(
        code, df
    ) or _load_cached_repaired_code(code, df)
    cache_hit = bool(cached_repaired_code)
    if cached_repaired_code:
        executable_code = cached_repaired_code

    deterministic_attempts = 2
    llm_repair_attempts = max(0, _env_int("MAX_CODE_REPAIR_ATTEMPTS", 0))
    total_attempts = deterministic_attempts + llm_repair_attempts
    last_exc = None
    for attempt in range(total_attempts):
        captured_stdout.clear()
        try:
            exec(executable_code, exec_namespace, exec_namespace)  # noqa: S102
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            repaired = False
            missing_key = _extract_key_error_name(exc)
            if missing_key:
                repaired = _repair_dataframe_column_alias(df, missing_key)

            missing_name = _extract_name_error_name(exc)
            if missing_name in {"pd", "np", "math", "df", "dfs"}:
                exec_namespace[missing_name] = {
                    "pd": pd,
                    "np": np,
                    "math": math,
                    "df": df,
                    "dfs": dfs,
                }[missing_name]
                repaired = True

            if (
                not repaired
                and not cache_hit
                and _env_flag("ENABLE_LLM_CODE_REPAIR", False)
                and attempt < total_attempts - 1
            ):
                repaired_code = _repair_code_with_llm(
                    code=executable_code,
                    exc=exc,
                    df=df,
                    ticker=ticker,
                    current_date=current_date,
                )
                if repaired_code and repaired_code != executable_code:
                    _save_cached_repaired_code(
                        original_code=code,
                        repaired_code=repaired_code,
                        df=df,
                        success=True,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    executable_code = repaired_code
                    repaired = True

            if not repaired or attempt == total_attempts - 1:
                return _to_text_response(
                    _format_execute_code_failure(
                        ticker=ticker,
                        current_date=current_date,
                        code=executable_code,
                        exc=exc,
                        df=df,
                    )
                )
            exec_namespace = make_namespace()

    if last_exc is not None:
        return _to_text_response(
            _format_execute_code_failure(
                ticker=ticker,
                current_date=current_date,
                code=executable_code,
                exc=last_exc,
                df=df,
            )
        )

    result = exec_namespace.get("result")

    if result is None:
        stdout = "".join(captured_stdout).strip()
        if stdout:
            return _to_text_response(
                f"=== execute_code ({ticker}, {current_date}) ===\n{stdout}"
            )

        tail = df.tail(5).to_string()
        return _to_text_response(
            f"=== execute_code ({ticker}, {current_date}) ===\n"
            "No `result` variable was set by the code.\n\n"
            "Recent data preview:\n"
            f"{tail}"
        )

    source = "cache" if cache_hit else "generated"
    if executable_code != code:
        source = "repaired" if not cache_hit else "validated_cache"
    _save_candidate_indicator_code(
        original_code=code,
        executable_code=executable_code,
        df=df,
        ticker=ticker,
        current_date=current_date,
        lookback_days=lookback_days,
        result=result,
        source=source,
    )

    if isinstance(result, pd.DataFrame):
        rendered = result.tail(10).to_string()
    elif isinstance(result, pd.Series):
        rendered = result.tail(10).to_string()
    else:
        rendered = str(result)

    stdout = "".join(captured_stdout).strip()
    if stdout:
        rendered = f"{rendered}\n\nCaptured stdout:\n{stdout}"

    return _to_text_response(
        f"=== execute_code ({ticker}, {current_date}) ===\n{rendered}"
    )


@safe
def run_indicator(
    indicator_id: str,
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Run a fixed, experiment-validated indicator by ID.

    This is the preferred path for repeatable backtests: indicator code is
    prepared and validated before the experiment, then referenced by ID during
    the backtest instead of being rewritten every trading day.
    """
    indicator = _load_experiment_indicator(indicator_id)
    if not indicator:
        available = _list_experiment_indicators()
        hint = ", ".join(available) if available else "none"
        return _to_text_response(
            f"[ERROR] Unknown experiment indicator: {indicator_id}. "
            f"Available indicators: {hint}. Run "
            "`python scripts/prepare_experiment_code.py <config>` first."
        )

    code = indicator.get("repaired_code") or indicator.get("original_code") or ""
    lookback_days = int(indicator.get("lookback_days") or 120)
    result = execute_code(
        tickers=tickers,
        current_date=current_date,
        code=code,
        lookback_days=lookback_days,
    )
    text = _text_response_content(result)
    return _to_text_response(
        f"=== run_indicator ({indicator_id}) ===\n"
        f"Description: {indicator.get('description', 'N/A')}\n\n"
        f"{text}"
    )


# ==================== Fundamental Analysis Tools ====================


@safe
def analyze_efficiency_ratios(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Analyze asset utilization efficiency ratios for stocks.

    Evaluates how efficiently companies use assets to generate revenue.
    Higher ratios generally indicate better operational efficiency.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of efficiency metrics for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Efficiency Ratios Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: No data available\n")
            continue

        m = metrics[0]
        lines.append(f"{ticker}:")
        lines.append(f"  Asset Turnover: {_fmt(m.asset_turnover)}")
        lines.append(f"  Inventory Turnover: {_fmt(m.inventory_turnover)}")
        lines.append(f"  Receivables Turnover: {_fmt(m.receivables_turnover)}")
        lines.append(
            f"  Working Capital Turnover: {_fmt(m.working_capital_turnover)}",
        )
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_profitability(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Analyze profitability metrics for stocks.

    Assesses how effectively companies generate profit from operations and equity.
    Higher margins indicate stronger profitability and better cost management.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of profitability metrics for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Profitability Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: No data available\n")
            continue

        m = metrics[0]
        roe = _safe_float(m.return_on_equity)
        net_margin = _safe_float(m.net_margin)
        op_margin = _safe_float(m.operating_margin)
        lines.append(f"{ticker}:")
        lines.append(f"  Return on Equity (ROE): {_fmt(roe/100, '.1%')}")
        lines.append(f"  Net Margin: {_fmt(net_margin/100, '.1%')}")
        lines.append(f"  Operating Margin: {_fmt(op_margin/100, '.1%')}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_growth(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Analyze growth metrics for stocks.

    Evaluates company growth trajectory across key financial dimensions.
    Higher growth rates may indicate strong business momentum.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of growth metrics for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Growth Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: No data available\n")
            continue

        m = metrics[0]
        lines.append(f"{ticker}:")
        lines.append(f"  Revenue Growth: {_fmt(m.revenue_growth, '.1%')}")
        lines.append(f"  Earnings Growth: {_fmt(m.earnings_growth, '.1%')}")
        lines.append(
            f"  Book Value Growth: {_fmt(m.book_value_growth, '.1%')}",
        )
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_financial_health(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Analyze financial health metrics for stocks.

    Assesses financial stability and ability to meet obligations.
    Strong financial health suggests lower bankruptcy risk.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of financial health metrics for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Financial Health Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: No data available\n")
            continue

        m = metrics[0]
        lines.append(f"{ticker}:")
        lines.append(
            f"  Current Ratio: {_fmt(m.current_ratio)} (>1 is healthy)",
        )
        lines.append(f"  Debt to Equity: {_fmt(m.debt_to_equity)}")
        lines.append(
            f"  Free Cash Flow/Share: ${_fmt(m.free_cash_flow_per_share)}",
        )
        lines.append(f"  EPS: ${_fmt(m.earnings_per_share)}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_valuation_ratios(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Analyze valuation ratios for stocks.

    Evaluates whether stocks are overvalued or undervalued using common multiples.
    Lower ratios may indicate undervaluation but compare with industry peers.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of valuation ratios for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Valuation Ratios Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: No data available\n")
            continue

        m = metrics[0]
        lines.append(f"{ticker}:")
        lines.append(f"  P/E Ratio: {_fmt(m.price_to_earnings_ratio)}")
        lines.append(f"  P/B Ratio: {_fmt(m.price_to_book_ratio)}")
        lines.append(f"  P/S Ratio: {_fmt(m.price_to_sales_ratio)}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def get_financial_metrics_tool(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
    period: str = "ttm",
) -> ToolResponse:
    """
    Get comprehensive financial metrics for stocks.

    Retrieves complete set of financial metrics for fundamental analysis.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.
        period: Time period - 'ttm', 'quarterly', or 'annual'. Default 'ttm'.

    Returns:
        Text summary of all available financial metrics for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [
        f"=== Comprehensive Financial Metrics ({current_date}, {period}) ===\n",
    ]

    for ticker in tickers:
        metrics = get_financial_metrics(
            ticker=ticker,
            end_date=current_date,
            period=period,
        )
        if not metrics:
            lines.append(f"{ticker}: No data available\n")
            continue

        m = metrics[0]
        lines.append(f"{ticker}:")
        lines.append(f"  Market Cap: ${_fmt(m.market_cap, ',.0f')}")
        lines.append(
            f"  P/E: {_fmt(m.price_to_earnings_ratio)} | P/B: {_fmt(m.price_to_book_ratio)} | P/S: {_fmt(m.price_to_sales_ratio)}",
        )
        lines.append(
            f"  ROE: {_fmt(m.return_on_equity, '.1%')} | Net Margin: {_fmt(m.net_margin, '.1%')}",
        )
        lines.append(
            f"  Revenue Growth: {_fmt(m.revenue_growth, '.1%')} | Earnings Growth: {_fmt(m.earnings_growth, '.1%')}",
        )
        lines.append(
            f"  Current Ratio: {_fmt(m.current_ratio)} | D/E: {_fmt(m.debt_to_equity)}",
        )
        lines.append(
            f"  EPS: ${_fmt(m.earnings_per_share)} | FCF/Share: ${_fmt(m.free_cash_flow_per_share)}",
        )
        lines.append("")

    return _to_text_response("\n".join(lines))


# ==================== Macro / Risk Adapter Tools ====================


@safe
def crawl_ths_news(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
    start_date: Optional[str] = None,
) -> ToolResponse:
    """
    Macro/news adapter tool.

    Thin wrapper around company news retrieval, formatted as raw evidence
    so the macro analyst can cite events before forming a view.
    """
    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    lines = [f"=== News Feed Snapshot ({current_date}) ===\n"]

    for ticker in tickers:
        news, trace = get_company_news_with_trace(
            ticker=ticker,
            end_date=current_date,
            start_date=start_date,
            limit=8,
        )
        lines.append(f"{ticker}: {_render_news_trace(trace)}")
        if not news:
            lines.append("  No recent news\n")
            continue

        for item in news[:5]:
            date_str = item.date[:10] if item.date else "N/A"
            lines.append(f"  - [{date_str}] {item.title}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def crawl_ths_concept(
    tickers: Optional[List[str]] = None,
) -> ToolResponse:
    """
    Concept/theme adapter for A-share names.

    Current version is a lightweight local mapping so the macro analyst can
    at least ground a stock in its sector/theme bucket before deeper analysis.
    """
    tickers = _normalize_tickers(tickers)
    lines = ["=== Concept Snapshot ===\n"]
    for ticker in tickers:
        concepts = _A_SHARE_CONCEPT_MAP.get(ticker, [])
        if concepts:
            lines.append(f"{ticker}: {', '.join(concepts)}")
        else:
            lines.append(f"{ticker}: No local concept tags available")
    return _to_text_response("\n".join(lines))


@safe
def crawl_ths_holder(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Top-shareholder / 筹码结构 tool.

    A股使用新浪十大股东（按公告日期前视过滤，取截至当日最新报告期）；
    其他市场退回 insider-trade proxy。
    """
    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    lines = [f"=== Top Shareholders ({current_date}) ===\n"]

    for ticker in tickers:
        holders = get_ashare_top_holders(ticker, current_date, top_n=10)
        if holders:
            lines.append(f"{ticker}: (截至 {holders[0].get('as_of', '?')})")
            for holder in holders:
                pct = holder.get("pct")
                pct_str = f"{pct}%" if pct is not None and str(pct) != "nan" else "?"
                lines.append(
                    f"  - {holder['name']}: {pct_str} {holder.get('nature', '')}"
                )
            lines.append("")
            continue

        # 非 A 股退回 insider proxy
        trades = get_insider_trades(
            ticker=ticker,
            end_date=current_date,
            limit=200,
        )
        if not trades:
            lines.append(f"{ticker}: No shareholder/insider data\n")
            continue

        share_delta = sum((trade.transaction_shares or 0.0) for trade in trades)
        activity = "net increase" if share_delta > 0 else "net decrease" if share_delta < 0 else "neutral"
        lines.append(f"{ticker}:")
        lines.append(f"  Insider/holder proxy trades: {len(trades)}")
        lines.append(f"  Net share delta: {share_delta:,.0f} -> {activity}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def crawl_ths_position(
    portfolio: Optional[dict] = None,
    current_prices: Optional[dict] = None,
    **_ignored: object,
) -> ToolResponse:
    """
    Portfolio position risk snapshot.

    This is the most directly useful risk tool for Day 12-13: it converts the
    current portfolio into exposure, concentration, and cash buffer facts.
    """
    portfolio = _portfolio_from_payload(portfolio)
    current_prices = current_prices or {}
    cash = float(portfolio.get("cash", 0.0) or 0.0)
    margin_used = float(portfolio.get("margin_used", 0.0) or 0.0)
    positions = portfolio.get("positions", {}) or {}

    total_value = cash + margin_used
    position_rows = []
    for ticker, position in positions.items():
        price = float(current_prices.get(ticker, 0.0) or 0.0)
        long_qty = float(position.get("long", 0) or 0)
        short_qty = float(position.get("short", 0) or 0)
        market_value = (long_qty - short_qty) * price
        total_value += market_value
        position_rows.append((ticker, long_qty, short_qty, price, market_value))

    lines = ["=== Portfolio Position Snapshot ===\n"]
    lines.append(f"Cash: {cash:,.2f}")
    lines.append(f"Margin Used: {margin_used:,.2f}")
    lines.append(f"Estimated Total Equity: {total_value:,.2f}")

    if not position_rows:
        lines.append("No open positions")
        return _to_text_response("\n".join(lines))

    lines.append("Positions:")
    largest_weight = 0.0
    largest_ticker = None
    for ticker, long_qty, short_qty, price, market_value in position_rows:
        weight = (market_value / total_value) if total_value else 0.0
        if abs(weight) > abs(largest_weight):
            largest_weight = weight
            largest_ticker = ticker
        lines.append(
            f"  - {ticker}: long={long_qty:.0f}, short={short_qty:.0f}, "
            f"price={price:.2f}, mv={market_value:,.2f}, weight={weight:.1%}"
        )
    if largest_ticker:
        lines.append(
            f"Largest Position Weight: {largest_ticker} ({largest_weight:.1%})"
        )

    return _to_text_response("\n".join(lines))


@safe
def crawl_ths_event(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Event-risk scan based on recent news headlines.

    Flags negative keywords so the risk agent has a concrete event list rather
    than free-form intuition.
    """
    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    negative_keywords = [
        "处罚", "违规", "问询", "下滑", "诉讼", "减持", "风险", "调查",
        "warning", "fraud", "lawsuit", "downgrade",
    ]

    lines = [f"=== Event Risk Snapshot ({current_date}) ===\n"]
    for ticker in tickers:
        news, trace = get_company_news_with_trace(
            ticker=ticker,
            end_date=current_date,
            limit=10,
        )
        lines.append(f"{ticker}: {_render_news_trace(trace)}")
        if not news:
            lines.append("  No recent event headlines\n")
            continue

        matched = []
        for item in news:
            if isinstance(item, dict):
                title = item.get("headline") or item.get("title") or ""
            else:
                title = getattr(item, "title", "") or getattr(item, "headline", "") or ""
            lower = title.lower()
            if any(keyword.lower() in lower for keyword in negative_keywords):
                matched.append(title)

        if matched:
            lines.append(f"  Negative Event Count: {len(matched)}")
            for title in matched[:5]:
                lines.append(f"  - {title}")
        else:
            lines.append("  No negative keyword events detected in recent headlines")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def crawl_ths_research(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    券商研报摘要（东财 stock_research_report_em，按日期前视过滤）。

    汇总机构评级分布 + 最近研报标题，作为机构预期/情绪信号供 sentiment 分析师引用。
    """
    from collections import Counter

    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    lines = [f"=== Analyst Research Reports ({current_date}) ===\n"]

    for ticker in tickers:
        reports = get_ashare_research_reports(ticker, current_date, limit=10)
        if not reports:
            lines.append(f"{ticker}: No research reports\n")
            continue

        ratings = Counter(r["rating"] for r in reports if r.get("rating"))
        rating_summary = ", ".join(f"{k}×{v}" for k, v in ratings.most_common()) or "无评级"
        lines.append(f"{ticker}: 近 {len(reports)} 份研报，评级分布 [{rating_summary}]")
        for report in reports[:5]:
            title = report["title"][:36]
            lines.append(
                f"  - [{report['date']}] {report['institution']} «{report['rating']}»: {title}"
            )
        lines.append("")

    return _to_text_response("\n".join(lines))


# ==================== Technical Analysis Tools ====================


@safe
def analyze_trend_following(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Trend following analysis using moving averages and MACD.

    Identifies market trends using SMA (20/50/200) and MACD indicators.
    Helps determine if stocks are in uptrend, downtrend, or consolidation.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of trend analysis for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Trend Following Analysis ({current_date}) ===\n"]

    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    # warmup 拉了 90 天历史，用 120 天窗口确保能覆盖 SMA60
    # 不要用 250 天，会超出 warmup 范围导致 Insufficient price data
    extended_start = (end_dt - timedelta(days=120)).strftime("%Y-%m-%d")

    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date=extended_start,
            end_date=current_date,
        )
        if not prices or len(prices) < 10:
            lines.append(f"{ticker}: Insufficient price data\n")
            continue

        df = prices_to_df(prices)
        n = len(df)

        # Calculate moving averages
        sma_20_win = min(20, n // 2)
        sma_50_win = min(50, n - 5) if n > 25 else min(25, n - 5)
        sma_200_win = min(200, n - 10) if n > 200 else None

        df["SMA_20"] = df["close"].rolling(window=sma_20_win).mean()
        df["SMA_50"] = df["close"].rolling(window=sma_50_win).mean()
        if sma_200_win:
            df["SMA_200"] = df["close"].rolling(window=sma_200_win).mean()

        df["EMA_12"] = df["close"].ewm(span=min(12, n // 3)).mean()
        df["EMA_26"] = df["close"].ewm(span=min(26, n // 2)).mean()
        df["MACD"] = df["EMA_12"] - df["EMA_26"]
        df["MACD_signal"] = df["MACD"].ewm(span=9).mean()

        current_price = _safe_float(df["close"].iloc[-1])
        sma_20 = _safe_float(df["SMA_20"].iloc[-1])
        sma_50 = _safe_float(df["SMA_50"].iloc[-1])
        sma_200 = (
            _safe_float(df["SMA_200"].iloc[-1])
            if "SMA_200" in df.columns
            else None
        )
        macd = _safe_float(df["MACD"].iloc[-1])
        macd_signal = _safe_float(df["MACD_signal"].iloc[-1])

        # Determine trend
        if sma_200:
            trend = "BULLISH" if current_price > sma_200 else "BEARISH"
            distance_200ma = ((current_price - sma_200) / sma_200) * 100
        else:
            trend = "UNKNOWN"
            distance_200ma = None

        macd_signal_str = "BUY" if macd > macd_signal else "SELL"

        lines.append(f"{ticker}: ${current_price:.2f}")
        lines.append(
            f"  SMA20: ${sma_20:.2f} | SMA50: ${sma_50:.2f} | SMA200: {f'${sma_200:.2f}' if sma_200 else 'N/A'}",
        )
        lines.append(
            f"  MACD: {macd:.3f} | Signal: {macd_signal:.3f} -> {macd_signal_str}",
        )
        lines.append(
            f"  Long-term Trend: {trend}"
            + (
                f" ({distance_200ma:+.1f}% from 200MA)"
                if distance_200ma
                else ""
            ),
        )
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_mean_reversion(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Mean reversion analysis using Bollinger Bands and RSI.

    Identifies overbought/oversold conditions.
    RSI >70 = overbought, <30 = oversold.
    Price near bands may signal reversal.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of mean reversion signals for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Mean Reversion Analysis ({current_date}) ===\n"]

    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    extended_start = (end_dt - timedelta(days=60)).strftime("%Y-%m-%d")

    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date=extended_start,
            end_date=current_date,
        )
        if not prices or len(prices) < 5:
            lines.append(f"{ticker}: Insufficient price data\n")
            continue

        df = prices_to_df(prices)
        n = len(df)

        # Bollinger Bands
        window = min(20, n - 2)
        df["SMA"] = df["close"].rolling(window=window).mean()
        df["STD"] = df["close"].rolling(window=window).std()
        df["Upper_Band"] = df["SMA"] + (2 * df["STD"])
        df["Lower_Band"] = df["SMA"] - (2 * df["STD"])

        # RSI
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        current_price = _safe_float(df["close"].iloc[-1])
        sma = _safe_float(df["SMA"].iloc[-1])
        upper = _safe_float(df["Upper_Band"].iloc[-1])
        lower = _safe_float(df["Lower_Band"].iloc[-1])
        rsi = _safe_float(df["RSI"].iloc[-1])
        deviation = (current_price - sma) / sma * 100

        # Signal interpretation
        if rsi > 70:
            rsi_signal = "OVERBOUGHT"
        elif rsi < 30:
            rsi_signal = "OVERSOLD"
        else:
            rsi_signal = "NEUTRAL"

        if current_price > upper:
            bb_signal = "ABOVE UPPER BAND (potential sell)"
        elif current_price < lower:
            bb_signal = "BELOW LOWER BAND (potential buy)"
        else:
            bb_signal = "WITHIN BANDS"

        lines.append(f"{ticker}: ${current_price:.2f}")
        lines.append(
            f"  Bollinger: Lower ${lower:.2f} | SMA ${sma:.2f} | Upper ${upper:.2f}",
        )
        lines.append(f"  Position: {bb_signal}")
        lines.append(f"  RSI: {rsi:.1f} -> {rsi_signal}")
        lines.append(f"  Price Deviation from SMA: {deviation:+.1f}%")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_momentum(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Momentum analysis for different time periods.

    Measures price momentum over 5, 10, and 20 day periods.
    Positive momentum indicates upward price pressure.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of momentum indicators for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Momentum Analysis ({current_date}) ===\n"]

    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    extended_start = (end_dt - timedelta(days=45)).strftime("%Y-%m-%d")

    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date=extended_start,
            end_date=current_date,
        )
        if not prices or len(prices) < 5:
            lines.append(f"{ticker}: Insufficient price data\n")
            continue

        df = prices_to_df(prices)
        n = len(df)
        df["returns"] = df["close"].pct_change()

        # Adaptive periods
        short_p = min(5, n // 3)
        med_p = min(10, n // 2)
        long_p = min(20, n - 2)

        current_price = _safe_float(df["close"].iloc[-1])
        mom_5 = (
            _safe_float(
                (df["close"].iloc[-1] / df["close"].iloc[-short_p - 1] - 1)
                * 100,
            )
            if n > short_p
            else 0
        )
        mom_10 = (
            _safe_float(
                (df["close"].iloc[-1] / df["close"].iloc[-med_p - 1] - 1)
                * 100,
            )
            if n > med_p
            else 0
        )
        mom_20 = (
            _safe_float(
                (df["close"].iloc[-1] / df["close"].iloc[-long_p - 1] - 1)
                * 100,
            )
            if n > long_p
            else 0
        )
        volatility = _safe_float(
            df["returns"].tail(20).std() * np.sqrt(252) * 100,
        )

        # Overall momentum signal
        avg_mom = (mom_5 + mom_10 + mom_20) / 3
        if avg_mom > 2:
            signal = "STRONG BULLISH"
        elif avg_mom > 0:
            signal = "BULLISH"
        elif avg_mom > -2:
            signal = "BEARISH"
        else:
            signal = "STRONG BEARISH"

        lines.append(f"{ticker}: ${current_price:.2f}")
        lines.append(
            f"  5-day: {mom_5:+.1f}% | 10-day: {mom_10:+.1f}% | 20-day: {mom_20:+.1f}%",
        )
        lines.append(f"  Volatility (annualized): {volatility:.1f}%")
        lines.append(f"  Overall: {signal}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def analyze_volatility(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Volatility analysis for different time windows.

    Measures price volatility over 10, 20, and 60 day periods.
    Higher volatility indicates higher risk but potentially higher returns.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date (YYYY-MM-DD). If None, uses date from context.

    Returns:
        Text summary of volatility metrics for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== Volatility Analysis ({current_date}) ===\n"]

    end_dt = datetime.strptime(current_date, "%Y-%m-%d")
    extended_start = (end_dt - timedelta(days=90)).strftime("%Y-%m-%d")

    for ticker in tickers:
        prices = get_prices(
            ticker=ticker,
            start_date=extended_start,
            end_date=current_date,
        )
        if not prices or len(prices) < 5:
            lines.append(f"{ticker}: Insufficient price data\n")
            continue

        df = prices_to_df(prices)
        n = len(df)
        df["returns"] = df["close"].pct_change()

        # Adaptive windows
        short_w = min(10, n // 2)
        med_w = min(20, n - 2)
        long_w = min(60, n - 1) if n > 30 else med_w

        current_price = _safe_float(df["close"].iloc[-1])
        vol_10 = _safe_float(
            df["returns"].tail(short_w).std() * np.sqrt(252) * 100,
        )
        vol_20 = _safe_float(
            df["returns"].tail(med_w).std() * np.sqrt(252) * 100,
        )
        vol_60 = _safe_float(
            df["returns"].tail(long_w).std() * np.sqrt(252) * 100,
        )

        # Risk assessment
        if vol_20 > 50:
            risk = "HIGH RISK"
        elif vol_20 > 25:
            risk = "MODERATE RISK"
        else:
            risk = "LOW RISK"

        lines.append(f"{ticker}: ${current_price:.2f}")
        lines.append(
            f"  10-day Vol: {vol_10:.1f}% | 20-day Vol: {vol_20:.1f}% | 60-day Vol: {vol_60:.1f}%",
        )
        lines.append(f"  Risk Level: {risk}")
        lines.append("")

    return _to_text_response("\n".join(lines))


# ==================== Sentiment Analysis Tools ====================


@safe
def analyze_news_sentiment(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
    start_date: Optional[str] = None,
) -> ToolResponse:
    """
    Analyze recent news for stocks.

    Retrieves and summarizes recent news articles.
    Use this to understand recent events and market sentiment.

    Args:
        tickers: List of stock tickers. If None, uses all tickers from context.
        current_date: Analysis date. If None, uses date from context.
        start_date: Optional start date for lookback period.

    Returns:
        Text summary of recent news for all tickers.
    """

    current_date = _resolved_date(current_date)
    lines = [f"=== News Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        news, trace = get_company_news_with_trace(
            ticker=ticker,
            end_date=current_date,
            start_date=start_date,
            limit=10,
        )

        lines.append(f"{ticker} - {_render_news_trace(trace)}")
        if not news:
            lines.append("  No recent news\n")
            continue

        lines.append(f"  {len(news)} recent articles:")
        for i, n in enumerate(news[:5], 1):
            date_str = n.date[:10] if n.date else "N/A"
            lines.append(f"  {i}. [{date_str}] {n.title[:80]}...")
            lines.append(f"     Source: {n.source}")
        if len(news) > 5:
            lines.append(f"  ... and {len(news) - 5} more articles")
        lines.append("")

    return _to_text_response("\n".join(lines))


# ==================== Valuation Analysis Tools ====================


@safe
def dcf_valuation_analysis(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    Discounted Cash Flow (DCF) valuation analysis（仅支持A股）。

    FCF = 经营现金流 - 资本支出（来自 akshare 现金流量表）
    Positive value_gap indicates potential undervaluation.
    """
    from backend.tools.data_tools import _is_a_share, _load_a_share_fcf_from_akshare, _get_a_share_market_cap

    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    lines = [f"=== DCF Valuation Analysis ({current_date}) ===\n"]

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date, limit=8)
        if not metrics:
            lines.append(f"{ticker}: No financial metrics\n")
            continue

        m = metrics[0]

        # ── A股路径 ──────────────────────────────────────────
        if _is_a_share(ticker):
            fcf_records = _load_a_share_fcf_from_akshare(ticker, current_date, limit=2)
            if not fcf_records or fcf_records[0]["fcf"] <= 0:
                lines.append(f"{ticker}: 现金流数据不足（OCF-Capex≤0，不适合DCF）\n")
                continue

            market_cap = _get_a_share_market_cap(ticker, current_date)
            if not market_cap:
                lines.append(f"{ticker}: 市值估算失败（缺少价格或总股本数据）\n")
                continue

            current_fcf = fcf_records[0]["fcf"]
            ocf = fcf_records[0]["ocf"]
            capex = fcf_records[0]["capex"]
            period = fcf_records[0]["period"]
        else:
            lines.append(f"{ticker}: 非A股标的，跳过DCF分析\n")
            continue

        # ── 共用 DCF 计算 ─────────────────────────────────────
        growth_rate = min(m.earnings_growth or 0.05, 0.30)   # 上限 30%
        discount_rate = 0.10
        terminal_growth = 0.03
        num_years = 5

        pv_fcf = sum(
            current_fcf * (1 + growth_rate) ** year / (1 + discount_rate) ** year
            for year in range(1, num_years + 1)
        )
        terminal_fcf = current_fcf * (1 + growth_rate) ** num_years * (1 + terminal_growth)
        terminal_value = terminal_fcf / (discount_rate - terminal_growth)
        pv_terminal = terminal_value / (1 + discount_rate) ** num_years
        enterprise_value = pv_fcf + pv_terminal
        value_gap = (enterprise_value - market_cap) / market_cap * 100

        if value_gap > 20:
            assessment = "SIGNIFICANTLY UNDERVALUED"
        elif value_gap > 0:
            assessment = "POTENTIALLY UNDERVALUED"
        elif value_gap > -20:
            assessment = "POTENTIALLY OVERVALUED"
        else:
            assessment = "SIGNIFICANTLY OVERVALUED"

        currency = "¥" if _is_a_share(ticker) else "$"
        lines.append(f"{ticker} (报告期 {period}):")
        if ocf is not None:
            lines.append(f"  经营现金流 (OCF): {currency}{ocf/1e8:,.1f}亿")
            lines.append(f"  资本支出  (Capex): {currency}{capex/1e8:,.1f}亿")
        lines.append(f"  自由现金流 (FCF): {currency}{current_fcf/1e8:,.1f}亿")
        lines.append(f"  成长率假设: {growth_rate:.1%}")
        lines.append(f"  DCF企业价值: {currency}{enterprise_value/1e8:,.0f}亿")
        lines.append(f"  当前市值:   {currency}{market_cap/1e8:,.0f}亿")
        lines.append(f"  价值偏差:   {value_gap:+.1f}% → {assessment}")
        lines.append("")

    return _to_text_response("\n".join(lines))


@safe
def a_share_valuation_analysis(
    tickers: Optional[List[str]] = None,
    current_date: Optional[str] = None,
) -> ToolResponse:
    """
    A股专用估值模型，替代不适用于A股的 DCF/EV-EBITDA 等模型。

    双模型并行：
    1. PB-ROE 模型（适合银行/金融股）
       公允PB = ROE / 股权成本(9%)
       若实际PB < 公允PB → 低估信号
    2. PE质量分位模型（适合消费/成长股）
       根据净利率+盈利增长+ROE给出质量档位，对应合理PE区间
       与当前PE对比得出高估/低估结论
    """
    current_date = _resolved_date(current_date)
    tickers = _normalize_tickers(tickers)
    lines = [f"=== A股估值分析 ({current_date}) ===\n"]

    COST_OF_EQUITY = 0.09  # A股股权成本假设

    for ticker in tickers:
        metrics = get_financial_metrics(ticker=ticker, end_date=current_date)
        if not metrics:
            lines.append(f"{ticker}: 无财务数据\n")
            continue

        m = metrics[0]
        roe        = _safe_float(m.return_on_equity)    # 小数形式，如 0.342
        net_margin = _safe_float(m.net_margin)          # 小数形式，如 0.525
        eg         = _safe_float(m.earnings_growth)     # 小数形式，如 0.192
        pb         = _safe_float(m.price_to_book_ratio) # 倍数，如 8.5
        pe         = _safe_float(m.price_to_earnings_ratio)

        # 尝试从价格缓存估算实际 PB / PE（若财务数据里没有）
        if (pb <= 0 or pe <= 0):
            prices = get_prices(ticker=ticker, start_date=current_date, end_date=current_date)
            if not prices:
                from datetime import datetime, timedelta
                fb_start = (datetime.strptime(current_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
                prices = get_prices(ticker=ticker, start_date=fb_start, end_date=current_date)
            # 价格有但 PB/PE 缺失时保留 0，后续模型降级处理

        lines.append(f"{ticker}:")
        lines.append(f"  ROE: {roe:.1%}  净利率: {net_margin:.1%}  盈利增长: {eg:.1%}")

        # ── 模型 1: PB-ROE ──────────────────────────────────
        fair_pb = roe / COST_OF_EQUITY if roe > 0 else None
        if fair_pb:
            lines.append(f"\n  [PB-ROE 模型]")
            lines.append(f"  公允PB = ROE({roe:.1%}) / 股权成本({COST_OF_EQUITY:.0%}) = {fair_pb:.2f}x")
            if pb > 0:
                pb_gap = (fair_pb - pb) / pb * 100
                if pb_gap > 20:
                    pb_signal = "低估 ✓"
                elif pb_gap > 0:
                    pb_signal = "略低估"
                elif pb_gap > -20:
                    pb_signal = "略高估"
                else:
                    pb_signal = "高估 ✗"
                lines.append(f"  实际PB = {pb:.2f}x  偏差 {pb_gap:+.1f}%  → {pb_signal}")
            else:
                lines.append(f"  实际PB 不可用（价格数据缺失），公允PB参考值: {fair_pb:.2f}x")
                if roe > COST_OF_EQUITY:
                    lines.append(f"  ROE({roe:.1%}) > 股权成本({COST_OF_EQUITY:.0%})，具备创造超额收益能力")

        # ── 模型 2: PE 质量分位 ──────────────────────────────
        lines.append(f"\n  [PE 质量分位模型]")
        # 质量评分：净利率+盈利增长+ROE 三维度
        quality_score = 0
        if net_margin > 0.30: quality_score += 3
        elif net_margin > 0.15: quality_score += 2
        elif net_margin > 0.05: quality_score += 1

        if eg > 0.20: quality_score += 3
        elif eg > 0.10: quality_score += 2
        elif eg > 0: quality_score += 1

        if roe > 0.25: quality_score += 3
        elif roe > 0.15: quality_score += 2
        elif roe > 0.08: quality_score += 1

        if quality_score >= 8:
            quality_tier = "顶级消费/科技成长"
            fair_pe_low, fair_pe_high = 30, 50
        elif quality_score >= 6:
            quality_tier = "优质成长"
            fair_pe_low, fair_pe_high = 20, 35
        elif quality_score >= 4:
            quality_tier = "普通成长"
            fair_pe_low, fair_pe_high = 12, 22
        else:
            quality_tier = "成熟/周期"
            fair_pe_low, fair_pe_high = 8, 15

        lines.append(f"  质量评分: {quality_score}/9  → 档位: {quality_tier}")
        lines.append(f"  合理PE区间: {fair_pe_low}x – {fair_pe_high}x")
        if pe > 0:
            if pe < fair_pe_low:
                pe_signal = f"低估（当前{pe:.1f}x < 合理下限{fair_pe_low}x）"
            elif pe <= fair_pe_high:
                pe_signal = f"合理（当前{pe:.1f}x 在区间内）"
            else:
                pe_signal = f"高估（当前{pe:.1f}x > 合理上限{fair_pe_high}x）"
            lines.append(f"  当前PE = {pe:.1f}x  → {pe_signal}")
        else:
            lines.append(f"  当前PE 不可用，参考合理区间 {fair_pe_low}x–{fair_pe_high}x 判断")

        lines.append("")

    return _to_text_response("\n".join(lines))


# Tool Registry for dynamic toolkit creation
TOOL_REGISTRY = {
    "extract_entities_code": extract_entities_code,
    "crawl_ths_finance": crawl_ths_finance,
    "history_calculate": history_calculate,
    "run_indicator": run_indicator,
    "execute_code": execute_code,
    "crawl_ths_news": crawl_ths_news,
    "crawl_ths_concept": crawl_ths_concept,
    "crawl_ths_holder": crawl_ths_holder,
    "crawl_ths_position": crawl_ths_position,
    "crawl_ths_event": crawl_ths_event,
    "crawl_ths_research": crawl_ths_research,
    "analyze_efficiency_ratios": analyze_efficiency_ratios,
    "analyze_profitability": analyze_profitability,
    "analyze_growth": analyze_growth,
    "analyze_financial_health": analyze_financial_health,
    "analyze_valuation_ratios": analyze_valuation_ratios,
    "get_financial_metrics_tool": get_financial_metrics_tool,
    "analyze_trend_following": analyze_trend_following,
    "analyze_mean_reversion": analyze_mean_reversion,
    "analyze_momentum": analyze_momentum,
    "analyze_volatility": analyze_volatility,
    "analyze_news_sentiment": analyze_news_sentiment,
    "dcf_valuation_analysis": dcf_valuation_analysis,
    "a_share_valuation_analysis": a_share_valuation_analysis,
}
