# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=C0301
"""
Data fetching tools for financial data.

All functions use centralized data source configuration from data_config.py.
The data source is automatically determined based on available API keys:
- Priority: FINNHUB_API_KEY > FINANCIAL_DATASETS_API_KEY
"""
import concurrent.futures
import datetime
import json
import os
import time
from pathlib import Path

import finnhub
import pandas as pd
import pandas_market_calendars as mcal
import requests

from backend.config.data_config import (
    get_config,
    get_api_key,
)
from backend.data.cache import get_cache
from backend.data.historical_price_manager import (
    _is_a_share,
    _load_from_disk_cache,
    _to_akshare_symbol,
)
from backend.data.schema import (
    CompanyFactsResponse,
    CompanyNews,
    CompanyNewsResponse,
    FinancialMetrics,
    FinancialMetricsResponse,
    InsiderTrade,
    InsiderTradeResponse,
    LineItem,
    LineItemResponse,
    Price,
    PriceResponse,
)
from backend.utils.settlement import logger


# Global cache instance
_cache = get_cache()

# ── Monkey-patch：修复 akshare 1.18.x 的 HTML 解析 bug ───────────────────────
# _stock_balance_sheet_by_report_ctype_em 抓 emweb 页面中的 <input id="hidctype">，
# 东方财富改版后该元素已不存在，soup.find() 返回 None，导致 None["value"] TypeError。
# 在模块加载时一次性打补丁，所有调用路径（backtest / download_data）都生效。
try:
    import akshare.stock_feature.stock_three_report_em as _em_mod

    _orig_ctype = _em_mod._stock_balance_sheet_by_report_ctype_em

    def _patched_ctype(symbol):
        try:
            return _orig_ctype(symbol)
        except (TypeError, AttributeError):
            return "通用"

    _em_mod._stock_balance_sheet_by_report_ctype_em = _patched_ctype
except Exception:
    pass
# ─────────────────────────────────────────────────────────────────────────────


def _akshare_call(fn, *args, timeout: int = 10, **kwargs):
    """
    Call any akshare function with a hard wall-clock timeout.

    Runs the akshare function in a separate thread and waits at most
    *timeout* seconds (default 10 s). If the call exceeds the limit,
    TimeoutError is raised so the caller can log a warning and return an
    empty result rather than hanging the whole analyst pipeline.

    Key detail: executor.shutdown(wait=False) — we do NOT wait for the
    background thread to finish. If akshare is stuck in a blocking
    socket read, we abandon it rather than hanging at the `with` exit.
    The orphaned thread will eventually die when the socket times out
    or the process exits.
    """
    _pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    _future = _pool.submit(fn, *args, **kwargs)
    try:
        return _future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        raise TimeoutError(f"akshare call timed out after {timeout}s")
    finally:
        _pool.shutdown(wait=False)  # 不等卡住的线程，直接放弃


_LOCAL_RET_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "ret_data"
_FINANCIAL_SNAPSHOT_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "financial_snapshots"
)
_COMPANY_NEWS_SNAPSHOT_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "company_news_snapshots"
)
# akshare 财务数据磁盘缓存：存放同花顺/东方财富财务数据的 parquet 文件
# 缓存一次后 backtest 全程读磁盘，VPN 失效也不影响
_AKSHARE_FINANCIAL_CACHE_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "akshare_financial_cache"
)


def _fin_cache_path(ticker: str, data_type: str) -> Path:
    """Return the pickle path for a given ticker + data_type."""
    _AKSHARE_FINANCIAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe = ticker.replace(".", "_")
    return _AKSHARE_FINANCIAL_CACHE_DIR / f"{safe}_{data_type}.pkl"


def _load_fin_cache(ticker: str, data_type: str) -> "pd.DataFrame | None":
    import pickle
    path = _fin_cache_path(ticker, data_type)
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
            df = pickle.load(f)
        return df if not df.empty else None
    except Exception as exc:
        logger.warning("financial cache read failed (%s/%s): %s", ticker, data_type, exc)
        return None


def _save_fin_cache(df: "pd.DataFrame", ticker: str, data_type: str) -> None:
    import pickle
    path = _fin_cache_path(ticker, data_type)
    try:
        with open(path, "wb") as f:
            pickle.dump(df, f)
        logger.debug("financial cache saved: %s", path.name)
    except Exception as exc:
        logger.warning("financial cache write failed (%s/%s): %s", ticker, data_type, exc)


def _financial_snapshot_path(
    ticker: str,
    end_date: str,
    period: str,
    limit: int,
) -> Path:
    _FINANCIAL_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe_ticker = ticker.replace(".", "_")
    return _FINANCIAL_SNAPSHOT_DIR / (
        f"{safe_ticker}_{period}_{end_date}_{limit}.json"
    )


def _company_news_snapshot_path(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    source: str,
) -> Path:
    _COMPANY_NEWS_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe_ticker = ticker.replace(".", "_")
    safe_start = (start_date or "none").replace("-", "")
    safe_end = end_date.replace("-", "")
    return _COMPANY_NEWS_SNAPSHOT_DIR / (
        f"{safe_ticker}_{source}_{safe_start}_{safe_end}_{limit}.json"
    )


def _save_financial_snapshot(
    ticker: str,
    end_date: str,
    period: str,
    limit: int,
    metrics: list[FinancialMetrics],
) -> None:
    path = _financial_snapshot_path(ticker, end_date, period, limit)
    try:
        payload = [metric.model_dump() for metric in metrics]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write financial snapshot for %s: %s", ticker, exc)


def _load_financial_snapshot(
    ticker: str,
    end_date: str,
    period: str,
    limit: int,
) -> list[FinancialMetrics]:
    path = _financial_snapshot_path(ticker, end_date, period, limit)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return [FinancialMetrics(**item) for item in payload]
        except Exception as exc:
            logger.warning("Failed to read financial snapshot %s: %s", path.name, exc)

    safe_ticker = ticker.replace(".", "_")
    candidates = sorted(
        _FINANCIAL_SNAPSHOT_DIR.glob(f"{safe_ticker}_{period}_*_*.json")
    )
    for candidate in reversed(candidates):
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            metrics = [FinancialMetrics(**item) for item in payload]
            if metrics:
                logger.info(
                    "Using nearest local financial snapshot for %s: %s",
                    ticker,
                    candidate.name,
                )
                return metrics
        except Exception as exc:
            logger.warning(
                "Failed to read candidate financial snapshot %s: %s",
                candidate.name,
                exc,
            )
    return []


def _fill_missing_financial_metrics(
    primary: list[FinancialMetrics],
    fallback: list[FinancialMetrics],
) -> list[FinancialMetrics]:
    if not primary or not fallback:
        return primary or fallback

    merged = []
    for idx, metric in enumerate(primary):
        fallback_metric = fallback[min(idx, len(fallback) - 1)]
        payload = metric.model_dump()
        fallback_payload = fallback_metric.model_dump()
        for field, value in payload.items():
            if value is None and fallback_payload.get(field) is not None:
                payload[field] = fallback_payload[field]
        merged.append(FinancialMetrics(**payload))
    return merged


def _save_company_news_snapshot(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    source: str,
    news: list[CompanyNews],
) -> None:
    path = _company_news_snapshot_path(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        source=source,
    )
    payload = {
        "ticker": ticker,
        "source": source,
        "start_date": start_date,
        "end_date": end_date,
        "limit": limit,
        "saved_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "news": [item.model_dump() for item in news],
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to write company news snapshot for %s: %s", ticker, exc)


def _filter_company_news_by_date(
    news: list[CompanyNews],
    start_date: str | None,
    end_date: str,
    limit: int,
) -> list[CompanyNews]:
    start_ts = pd.to_datetime(start_date) if start_date else None
    end_ts = pd.to_datetime(end_date)
    filtered = []

    for item in news:
        item_ts = pd.to_datetime(item.date, errors="coerce")
        if pd.notna(item_ts):
            if item_ts > end_ts:
                continue
            if start_ts is not None and item_ts < start_ts:
                continue
        filtered.append(item)

    filtered.sort(
        key=lambda item: pd.to_datetime(item.date, errors="coerce"),
        reverse=True,
    )
    return filtered[:limit]


def _load_company_news_snapshot(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    source: str,
) -> list[CompanyNews]:
    path = _company_news_snapshot_path(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        source=source,
    )
    candidates = [path]

    safe_ticker = ticker.replace(".", "_")
    if not path.exists():
        pattern = f"{safe_ticker}_{source}_*_*_*.json"
        candidates.extend(
            sorted(_COMPANY_NEWS_SNAPSHOT_DIR.glob(pattern), reverse=True)
        )

    seen = set()
    for candidate in candidates:
        if candidate in seen or not candidate.exists():
            continue
        seen.add(candidate)
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
            news_payload = payload.get("news", payload)
            news = [CompanyNews(**item) for item in news_payload]
            filtered = _filter_company_news_by_date(news, start_date, end_date, limit)
            if filtered:
                if candidate != path:
                    logger.info(
                        "Using nearest company news snapshot for %s: %s",
                        ticker,
                        candidate.name,
                    )
                return filtered
        except Exception as exc:
            logger.warning(
                "Failed to read company news snapshot %s: %s",
                candidate.name,
                exc,
            )

    return []


def _parse_cn_number(value):
    """Parse Chinese numeric strings like '823.20亿' / '1.47万' / '0.0013'."""
    if value in (None, False, "", "False", "None", "-", "--"):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if text in {"", "False", "None", "-", "--"}:
        return None

    multiplier = 1.0
    if text.endswith("亿"):
        multiplier = 1e8
        text = text[:-1]
    elif text.endswith("万"):
        multiplier = 1e4
        text = text[:-1]

    try:
        return float(text) * multiplier
    except ValueError:
        return None


def _load_a_share_fcf_from_akshare(
    ticker: str,
    end_date: str,
    limit: int = 2,
) -> list[dict]:
    """
    Load A-share Free Cash Flow = 经营现金流 - 资本支出 from akshare.

    Returns list of dicts: [{"period": "2023-09-30", "ocf": 1.2e10, "capex": 3e9, "fcf": 9e9}]
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    ak_symbol = _to_akshare_symbol(ticker)

    # ── 磁盘缓存：优先读取（东方财富格式 / 同花顺格式 分开存） ───────────────
    _ths_format = False
    df = _load_fin_cache(ticker, "cash_flow")
    if df is None:
        df = _load_fin_cache(ticker, "cash_flow_ths")
        if df is not None:
            _ths_format = True

    if df is None:
        # ① 东方财富：按报告期 → 按年度
        for fetch_fn_name in ("stock_cash_flow_sheet_by_report_em",
                              "stock_cash_flow_sheet_by_yearly_em"):
            fetch_fn = getattr(ak, fetch_fn_name, None)
            if fetch_fn is None:
                continue
            try:
                df = _akshare_call(fetch_fn, symbol=ak_symbol)
                if df is not None and not df.empty:
                    _save_fin_cache(df, ticker, "cash_flow")
                    break
                df = None
            except Exception as exc:
                logger.warning("akshare cash flow [%s] failed for %s: %s",
                               fetch_fn_name, ticker, exc)
                df = None

    if df is None:
        # ② 同花顺备选（VPN 下东方财富失败时使用）
        try:
            df = _akshare_call(
                ak.stock_financial_cash_ths,
                symbol=ak_symbol,
                indicator="按报告期",
            )
            if df is not None and not df.empty:
                _save_fin_cache(df, ticker, "cash_flow_ths")
                _ths_format = True
            else:
                df = None
        except Exception as exc:
            logger.warning("akshare cash flow ths failed for %s: %s", ticker, exc)
            df = None

    if df is None or df.empty:
        return []

    # ── 格式规整：统一转为「行=报告期（DatetimeIndex），列=科目」────────────
    try:
        df = df.copy()
        if _ths_format:
            # 同花顺格式：行已是报告期，第一列为 '报告期'
            df["报告期"] = pd.to_datetime(df["报告期"], errors="coerce")
            df = df.set_index("报告期")
            df = df.drop(
                columns=["报表核心指标", "报表全部指标", "补充资料："],
                errors="ignore",
            )
        else:
            # 东方财富格式：行=科目，第一列=科目名，其余列=日期 → 转置
            df = df.set_index(df.columns[0]).T
            df.index = pd.to_datetime(df.index, errors="coerce")
        df = df[df.index.notna()].sort_index()
    except Exception as exc:
        logger.warning("akshare cash flow reshape failed for %s: %s", ticker, exc)
        return []

    # 过滤到 end_date 之前的报告期
    target = pd.Timestamp(end_date)
    df = df[df.index <= target]
    if df.empty:
        return []

    df = df.tail(limit)

    results = []
    for period, row in df.iterrows():
        # 查找 经营活动现金流量净额（可能有不同列名）
        ocf = None
        for col in row.index:
            col_str = str(col)
            if "经营活动" in col_str and "净额" in col_str:
                ocf = _parse_cn_number(row[col])
                break

        # 查找资本支出（购建固定资产等）
        capex = None
        for col in row.index:
            col_str = str(col)
            if "购建固定资产" in col_str or "购置固定资产" in col_str:
                capex = _parse_cn_number(row[col])
                break

        if ocf is not None:
            fcf = ocf - (capex or 0)
            results.append({
                "period": period.strftime("%Y-%m-%d"),
                "ocf": ocf,
                "capex": capex or 0,
                "fcf": fcf,
            })

    return results


def _get_a_share_market_cap(
    ticker: str,
    end_date: str,
    net_income: float | None = None,
    eps: float | None = None,
) -> float | None:
    """
    Estimate A-share market cap = 收盘价 × 总股本.

    总股本由财务摘要数据推算：total_shares = net_income / eps
    （两者来自同一报告期行，单位一致，结果为股数）

    不依赖任何外部网络接口，只用本地价格缓存和已有财务数据。
    """
    if not net_income or not eps or eps == 0:
        return None

    total_shares = net_income / eps  # 单位：股

    # 用本地价格缓存获取收盘价
    from datetime import datetime, timedelta
    prices = get_prices(ticker=ticker, start_date=end_date, end_date=end_date)
    if not prices:
        fallback_start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
        prices = get_prices(ticker=ticker, start_date=fallback_start, end_date=end_date)
    if not prices:
        return None

    return prices[-1].close * total_shares


def _parse_cn_percent(value):
    """Parse percentage strings like '32.53%' into 0.3253."""
    if value in (None, False, "", "False", "None", "-", "--"):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text) / 100
    except ValueError:
        return None


def _load_a_share_financial_metrics_from_akshare(
    ticker: str,
    end_date: str,
    period: str,
) -> list[FinancialMetrics]:
    """
    Load A-share financial snapshot from akshare.

    当前优先用同花顺财务摘要接口，覆盖 fundamentals 关心的核心字段。
    未覆盖字段保留 None，后续可继续扩展。
    """
    try:
        import akshare as ak
    except ImportError:
        return []

    ak_symbol = _to_akshare_symbol(ticker)

    # ── 磁盘缓存：优先读取，避免每次回测都走网络 ──────────────────────────
    df = _load_fin_cache(ticker, "financial_abstract")
    if df is None:
        try:
            df = _akshare_call(ak.stock_financial_abstract_ths, symbol=ak_symbol)
            if df is not None and not df.empty:
                _save_fin_cache(df, ticker, "financial_abstract")
        except Exception as exc:
            logger.warning("akshare financial abstract failed for %s: %s", ticker, exc)
            return []

    if df is None or df.empty or "报告期" not in df.columns:
        return []

    try:
        df = df.copy()
        df["报告期"] = pd.to_datetime(df["报告期"])
        target_date = pd.Timestamp(end_date)
        valid = df[df["报告期"] <= target_date]
        if valid.empty:
            row = df.iloc[-1]
        else:
            row = valid.sort_values("报告期").iloc[-1]
    except Exception:
        row = df.iloc[-1]

    report_period = pd.Timestamp(row["报告期"]).strftime("%Y-%m-%d")

    metric = FinancialMetrics(
        ticker=ticker,
        report_period=report_period,
        period=period,
        currency="CNY",
        market_cap=None,
        enterprise_value=None,
        price_to_earnings_ratio=None,
        price_to_book_ratio=None,
        price_to_sales_ratio=None,
        enterprise_value_to_ebitda_ratio=None,
        enterprise_value_to_revenue_ratio=None,
        free_cash_flow_yield=None,
        peg_ratio=None,
        gross_margin=_parse_cn_percent(row.get("销售毛利率")),
        operating_margin=None,
        net_margin=_parse_cn_percent(row.get("销售净利率")),
        return_on_equity=_parse_cn_percent(row.get("净资产收益率")),
        return_on_assets=None,
        return_on_invested_capital=None,
        asset_turnover=None,
        inventory_turnover=_parse_cn_number(row.get("存货周转率")),
        receivables_turnover=None,
        days_sales_outstanding=_parse_cn_number(row.get("应收账款周转天数")),
        operating_cycle=_parse_cn_number(row.get("营业周期")),
        working_capital_turnover=None,
        current_ratio=_parse_cn_number(row.get("流动比率")),
        quick_ratio=_parse_cn_number(row.get("速动比率")),
        cash_ratio=None,
        operating_cash_flow_ratio=None,
        debt_to_equity=_parse_cn_number(row.get("产权比率")),
        debt_to_assets=_parse_cn_percent(row.get("资产负债率")),
        interest_coverage=None,
        revenue_growth=_parse_cn_percent(row.get("营业总收入同比增长率")),
        earnings_growth=_parse_cn_percent(row.get("净利润同比增长率")),
        book_value_growth=None,
        earnings_per_share_growth=None,
        free_cash_flow_growth=None,
        operating_income_growth=None,
        ebitda_growth=None,
        payout_ratio=None,
        earnings_per_share=_parse_cn_number(row.get("基本每股收益")),
        book_value_per_share=_parse_cn_number(row.get("每股净资产")),
        free_cash_flow_per_share=_parse_cn_number(row.get("每股经营现金流")),
    )

    # ── 补全 PE / PB / market_cap（基于当日收盘价 × 每股数据）──────────────
    # 全程使用本地缓存，不走网络，不依赖任何东财接口
    try:
        from datetime import datetime, timedelta
        _prices = get_prices(ticker=ticker, start_date=end_date, end_date=end_date)
        if not _prices:
            _fb_start = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
            _prices = get_prices(ticker=ticker, start_date=_fb_start, end_date=end_date)
        if _prices:
            _close = _prices[-1].close
            _eps = metric.earnings_per_share
            _bvps = metric.book_value_per_share
            _pe = round(_close / _eps, 2) if _eps and _eps > 0 else None
            _pb = round(_close / _bvps, 2) if _bvps and _bvps > 0 else None
            # market_cap = 收盘价 × 总股本，总股本由 net_income/EPS 推算，无需外部接口
            _net_income = _parse_cn_number(row.get("净利润"))
            _mktcap = _get_a_share_market_cap(
                ticker, end_date, net_income=_net_income, eps=_eps
            )
            metric = metric.model_copy(update={
                "price_to_earnings_ratio": _pe,
                "price_to_book_ratio": _pb,
                "market_cap": _mktcap,
            })
    except Exception as _exc:
        logger.debug("PE/PB/market_cap enrichment failed for %s: %s", ticker, _exc)

    return [metric]


def _load_local_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> list[Price]:
    """Load backtest prices from local CSV files when API keys are absent."""
    csv_path = _LOCAL_RET_DATA_DIR / f"{ticker}.csv"
    if not csv_path.exists():
        if _is_a_share(ticker):
            cache_df = _load_from_disk_cache(ticker, start_date, end_date)
            if cache_df is None or cache_df.empty:
                safe_symbol = ticker.replace(".", "_")
                cache_dir = Path(__file__).resolve().parent.parent / "data" / "akshare_cache"
                candidates = sorted(cache_dir.glob(f"{safe_symbol}_*.parquet"))
                if candidates:
                    try:
                        cache_df = pd.read_parquet(candidates[-1])
                    except Exception:
                        cache_df = None

            if cache_df is None or cache_df.empty:
                return []

            filtered = cache_df.loc[
                (cache_df.index >= pd.Timestamp(start_date))
                & (cache_df.index <= pd.Timestamp(end_date))
            ].copy()
            if filtered.empty:
                return []

            return [
                Price(
                    open=float(row["open"]),
                    close=float(row["close"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    volume=int(row.get("volume", 0) or 0),
                    time=pd.Timestamp(idx).strftime("%Y-%m-%d"),
                )
                for idx, row in filtered.iterrows()
            ]
        return []

    df = pd.read_csv(csv_path)
    if df.empty or "time" not in df.columns:
        return []

    mask = (df["time"] >= start_date) & (df["time"] <= end_date)
    filtered = df.loc[mask].copy()
    if filtered.empty:
        return []

    return [
        Price(
            open=float(row["open"]),
            close=float(row["close"]),
            high=float(row["high"]),
            low=float(row["low"]),
            volume=int(row["volume"]),
            time=str(row["time"]),
        )
        for _, row in filtered.iterrows()
    ]


def get_last_tradeday(date: str) -> str:
    """
    Get the previous trading day for the specified date

    Args:
        date: Date string (YYYY-MM-DD)

    Returns:
        Previous trading day date string (YYYY-MM-DD)
    """
    current_date = datetime.datetime.strptime(date, "%Y-%m-%d")
    _NYSE_CALENDAR = mcal.get_calendar("NYSE")

    if _NYSE_CALENDAR is not None:
        # Get trading days before current date
        # Go back 90 days from current date to get all trading days
        start_search = current_date - datetime.timedelta(days=90)

        if hasattr(_NYSE_CALENDAR, "valid_days"):
            # pandas_market_calendars
            trading_dates = _NYSE_CALENDAR.valid_days(
                start_date=start_search.strftime("%Y-%m-%d"),
                end_date=current_date.strftime("%Y-%m-%d"),
            )
        else:
            # exchange_calendars
            trading_dates = _NYSE_CALENDAR.sessions_in_range(
                start_search.strftime("%Y-%m-%d"),
                current_date.strftime("%Y-%m-%d"),
            )

        # Convert to date list
        trading_dates_list = [
            pd.Timestamp(d).strftime("%Y-%m-%d") for d in trading_dates
        ]

        # Find current date position in the list
        if date in trading_dates_list:
            # If current date is a trading day, return previous trading day
            idx = trading_dates_list.index(date)
            if idx > 0:
                return trading_dates_list[idx - 1]
            else:
                # If it's the first trading day, go back further
                prev_date = current_date - datetime.timedelta(days=1)
                return get_last_tradeday(prev_date.strftime("%Y-%m-%d"))
        else:
            # If current date is not a trading day, return the nearest trading day
            if trading_dates_list:
                return trading_dates_list[-1]

    return prev_date.strftime("%Y-%m-%d")


def _make_api_request(
    url: str,
    headers: dict,
    method: str = "GET",
    json_data: dict = None,
    max_retries: int = 0,
) -> requests.Response:
    """
    Make an API request with rate limiting handling and moderate backoff.

    Args:
        url: The URL to request
        headers: Headers to include in the request
        method: HTTP method (GET or POST)
        json_data: JSON data for POST requests
        max_retries: Maximum number of retries (default: 3)

    Returns:
        requests.Response: The response object

    Raises:
        Exception: If the request fails with a non-429 error
    """
    for attempt in range(max_retries + 1):  # +1 for initial attempt
        if method.upper() == "POST":
            response = requests.post(url, headers=headers, json=json_data, timeout=8)
        else:
            response = requests.get(url, headers=headers, timeout=8)

        if response.status_code == 429 and attempt < max_retries:
            # Linear backoff: 60s, 90s, 120s, 150s...
            delay = 60 + (30 * attempt)
            print(
                f"Rate limited (429). Attempt {attempt + 1}/{max_retries + 1}. Waiting {delay}s before retrying...",
            )
            time.sleep(delay)
            continue

        # Return the response (whether success, other errors, or final 429)
        return response


def get_prices(
    ticker: str,
    start_date: str,
    end_date: str,
) -> list[Price]:
    """
    A股价格数据获取，走 akshare（磁盘缓存 → 网络拉取）。

    Args:
        ticker: A股 ticker，如 600519.SH
        start_date: 开始日期 YYYY-MM-DD
        end_date:   结束日期 YYYY-MM-DD

    Returns:
        list[Price]
    """
    from backend.data.historical_price_manager import (
        _load_akshare,
        _load_from_disk_cache,
        _price_cache_only,
    )

    cache_key = f"{ticker}_{start_date}_{end_date}_akshare"
    if cached_data := _cache.get_prices(cache_key):
        return [Price(**p) for p in cached_data]

    df = _load_from_disk_cache(ticker, start_date, end_date)

    # ── 宽松磁盘回退：date range 不完全覆盖时，找该 ticker 任意 parquet ────
    # 场景：工具请求 250 天历史（200 日均线），但只下载了 90 天预热数据。
    # 与其走 akshare 网络（VPN 下挂死），不如用已有数据取交集。
    if df is None or df.empty:
        from pathlib import Path as _Path
        _cache_dir = _Path(__file__).resolve().parent.parent / "data" / "akshare_cache"
        _safe = ticker.replace(".", "_")
        for _candidate in sorted(_cache_dir.glob(f"{_safe}_*.parquet"), reverse=True):
            try:
                _df_any = pd.read_parquet(_candidate)
                if not _df_any.empty:
                    df = _df_any
                    break
            except Exception:
                continue

    if (df is None or df.empty) and not _price_cache_only():
        df = _load_akshare(ticker, start_date, end_date)
    if df is None or df.empty:
        logger.warning("akshare 无价格数据: %s [%s, %s]", ticker, start_date, end_date)
        return []

    df_filtered = df.loc[
        (df.index >= pd.Timestamp(start_date))
        & (df.index <= pd.Timestamp(end_date))
    ]
    prices = [
        Price(
            open=float(row["open"]),
            close=float(row["close"]),
            high=float(row.get("high", row["close"])),
            low=float(row.get("low", row["close"])),
            volume=int(row.get("volume", 0) or 0),
            time=pd.Timestamp(idx).strftime("%Y-%m-%d"),
        )
        for idx, row in df_filtered.iterrows()
    ]
    _cache.set_prices(cache_key, [p.model_dump() for p in prices])
    return prices


def get_financial_metrics(
    ticker: str,
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[FinancialMetrics]:
    """
    Fetch financial metrics from cache or API.

    Uses centralized data source configuration (FINNHUB_API_KEY prioritized).

    Args:
        ticker: Stock ticker symbol
        end_date: End date (YYYY-MM-DD)
        period: Period type (default: "ttm")
        limit: Number of records to fetch

    Returns:
        list[FinancialMetrics]: List of financial metrics
    """
    config = get_config()
    data_source = config.source
    api_key = config.api_key

    # Create a cache key that includes all parameters to ensure exact matches
    cache_key = f"{ticker}_{period}_{end_date}_{limit}_{data_source}"

    # Check cache first - simple exact match
    if cached_data := _cache.get_financial_metrics(cache_key):
        return [FinancialMetrics(**metric) for metric in cached_data]

    financial_metrics = []

    if _is_a_share(ticker):
        snapshot_metrics = _load_financial_snapshot(
            ticker,
            end_date,
            period,
            limit,
        )
        financial_metrics = _load_a_share_financial_metrics_from_akshare(
            ticker=ticker,
            end_date=end_date,
            period=period,
        )
        financial_metrics = _fill_missing_financial_metrics(
            financial_metrics,
            snapshot_metrics,
        )
        if financial_metrics:
            _cache.set_financial_metrics(
                cache_key,
                [m.model_dump() for m in financial_metrics],
            )
            _save_financial_snapshot(
                ticker=ticker,
                end_date=end_date,
                period=period,
                limit=limit,
                metrics=financial_metrics,
            )
        return financial_metrics

    if data_source == "finnhub":
        try:
            # Use Finnhub API - Basic Financials
            client = finnhub.Client(api_key=api_key)

            # Fetch basic financials from Finnhub
            # metric='all' returns all available metrics
            financials = client.company_basic_financials(ticker, "all")

            if not financials or "metric" not in financials:
                return []

            # Finnhub returns {series: {...}, metric: {...}, metricType: ..., symbol: ...}
            # We need to create a FinancialMetrics object from this
            metric_data = financials.get("metric", {})

            # Create a FinancialMetrics object with available data
            metric = _map_finnhub_metrics(ticker, end_date, period, metric_data)

            financial_metrics = [metric]
        except Exception as exc:
            logger.warning(
                "Falling back to local financial snapshot for %s because Finnhub "
                "financials failed: %s",
                ticker,
                exc,
            )
            financial_metrics = _load_financial_snapshot(
                ticker,
                end_date,
                period,
                limit,
            )

    else:  # financial_datasets
        try:
            # Use Financial Datasets API
            headers = {"X-API-KEY": api_key}

            url = f"https://api.financialdatasets.ai/financial-metrics/?ticker={ticker}&report_period_lte={end_date}&limit={limit}&period={period}"
            response = _make_api_request(url, headers)
            if response.status_code != 200:
                raise ValueError(
                    f"Error fetching data: {ticker} - {response.status_code} - {response.text}",
                )

            # Parse response with Pydantic model
            metrics_response = FinancialMetricsResponse(**response.json())
            financial_metrics = metrics_response.financial_metrics
        except Exception as exc:
            logger.warning(
                "Falling back to local financial snapshot for %s because Financial "
                "Datasets financials failed: %s",
                ticker,
                exc,
            )
            financial_metrics = _load_financial_snapshot(
                ticker,
                end_date,
                period,
                limit,
            )

    if not financial_metrics:
        return []

    # Cache the results as dicts using the comprehensive cache key
    _cache.set_financial_metrics(
        cache_key,
        [m.model_dump() for m in financial_metrics],
    )
    _save_financial_snapshot(
        ticker=ticker,
        end_date=end_date,
        period=period,
        limit=limit,
        metrics=financial_metrics,
    )
    return financial_metrics


def _map_finnhub_metrics(
    ticker: str,
    end_date: str,
    period: str,
    metric_data: dict,
) -> FinancialMetrics:
    """Map Finnhub metric data to FinancialMetrics model."""
    return FinancialMetrics(
        ticker=ticker,
        report_period=end_date,
        period=period,
        currency="USD",
        market_cap=metric_data.get("marketCapitalization"),
        enterprise_value=None,
        price_to_earnings_ratio=metric_data.get("peBasicExclExtraTTM"),
        price_to_book_ratio=metric_data.get("pbAnnual"),
        price_to_sales_ratio=metric_data.get("psAnnual"),
        enterprise_value_to_ebitda_ratio=None,
        enterprise_value_to_revenue_ratio=None,
        free_cash_flow_yield=None,
        peg_ratio=None,
        gross_margin=metric_data.get("grossMarginTTM"),
        operating_margin=metric_data.get("operatingMarginTTM"),
        net_margin=metric_data.get("netProfitMarginTTM"),
        return_on_equity=metric_data.get("roeTTM"),
        return_on_assets=metric_data.get("roaTTM"),
        return_on_invested_capital=metric_data.get("roicTTM"),
        asset_turnover=metric_data.get("assetTurnoverTTM"),
        inventory_turnover=metric_data.get("inventoryTurnoverTTM"),
        receivables_turnover=metric_data.get("receivablesTurnoverTTM"),
        days_sales_outstanding=None,
        operating_cycle=None,
        working_capital_turnover=None,
        current_ratio=metric_data.get("currentRatioAnnual"),
        quick_ratio=metric_data.get("quickRatioAnnual"),
        cash_ratio=None,
        operating_cash_flow_ratio=None,
        debt_to_equity=metric_data.get("totalDebt/totalEquityAnnual"),
        debt_to_assets=None,
        interest_coverage=None,
        revenue_growth=metric_data.get("revenueGrowthTTMYoy"),
        earnings_growth=None,
        book_value_growth=None,
        earnings_per_share_growth=metric_data.get("epsGrowthTTMYoy"),
        free_cash_flow_growth=None,
        operating_income_growth=None,
        ebitda_growth=None,
        payout_ratio=metric_data.get("payoutRatioAnnual"),
        earnings_per_share=metric_data.get("epsBasicExclExtraItemsTTM"),
        book_value_per_share=metric_data.get("bookValuePerShareAnnual"),
        free_cash_flow_per_share=None,
    )


def search_line_items(
    ticker: str,
    line_items: list[str],
    end_date: str,
    period: str = "ttm",
    limit: int = 10,
) -> list[LineItem]:
    """
    Fetch line items from Financial Datasets API (only supported source).

    Returns empty list on API errors to allow graceful degradation.
    """
    try:
        api_key = get_api_key()
        headers = {"X-API-KEY": api_key}

        url = "https://api.financialdatasets.ai/financials/search/line-items"
        body = {
            "tickers": [ticker],
            "line_items": line_items,
            "end_date": end_date,
            "period": period,
            "limit": limit,
        }
        response = _make_api_request(
            url,
            headers,
            method="POST",
            json_data=body,
        )

        if response.status_code != 200:
            logger.info(
                f"Warning: Failed to fetch line items for {ticker}: "
                f"{response.status_code} - {response.text}",
            )
            return []

        data = response.json()
        response_model = LineItemResponse(**data)
        search_results = response_model.search_results

        if not search_results:
            return []

        return search_results[:limit]

    except Exception as e:
        logger.info(
            f"Warning: Exception while fetching line items for {ticker}: {str(e)}",
        )
        return []


def _fetch_finnhub_insider_trades(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    api_key: str,
) -> list[InsiderTrade]:
    """Fetch insider trades from Finnhub API."""
    client = finnhub.Client(api_key=api_key)

    from_date = start_date or (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        - datetime.timedelta(days=365)
    ).strftime("%Y-%m-%d")

    insider_data = client.stock_insider_transactions(
        ticker,
        from_date,
        end_date,
    )

    if not insider_data or "data" not in insider_data:
        return []

    return [
        _convert_finnhub_insider_trade(ticker, trade)
        for trade in insider_data["data"][:limit]
    ]


def _fetch_fd_insider_trades(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    api_key: str,
) -> list[InsiderTrade]:
    """Fetch insider trades from Financial Datasets API."""
    headers = {"X-API-KEY": api_key}
    all_trades = []
    current_end_date = end_date

    while True:
        url = f"https://api.financialdatasets.ai/insider-trades/?ticker={ticker}&filing_date_lte={current_end_date}"
        if start_date:
            url += f"&filing_date_gte={start_date}"
        url += f"&limit={limit}"

        response = _make_api_request(url, headers)
        if response.status_code != 200:
            raise ValueError(
                f"Error fetching data: {ticker} - {response.status_code} - {response.text}",
            )

        data = response.json()
        response_model = InsiderTradeResponse(**data)
        insider_trades = response_model.insider_trades

        if not insider_trades:
            break

        all_trades.extend(insider_trades)

        if not start_date or len(insider_trades) < limit:
            break

        current_end_date = min(
            trade.filing_date for trade in insider_trades
        ).split("T")[0]

        if current_end_date <= start_date:
            break

    return all_trades


def get_insider_trades(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[InsiderTrade]:
    """Fetch insider trades from cache or API."""
    if _is_a_share(ticker):
        return []

    config = get_config()
    data_source = config.source
    api_key = config.api_key

    cache_key = (
        f"{ticker}_{start_date or 'none'}_{end_date}_{limit}_{data_source}"
    )

    if cached_data := _cache.get_insider_trades(cache_key):
        return [InsiderTrade(**trade) for trade in cached_data]

    if data_source == "finnhub":
        all_trades = _fetch_finnhub_insider_trades(
            ticker,
            start_date,
            end_date,
            limit,
            api_key,
        )
    else:
        all_trades = _fetch_fd_insider_trades(
            ticker,
            start_date,
            end_date,
            limit,
            api_key,
        )

    if not all_trades:
        return []

    _cache.set_insider_trades(
        cache_key,
        [trade.model_dump() for trade in all_trades],
    )
    return all_trades


def _crawl_cache_only() -> bool:
    """A_SHARE_CRAWL_CACHE_ONLY=1 时，研报/股东/公告等爬虫缓存未命中即跳过联网。

    回测全程读盘、可复现，且避免东财公告接口在差网络下 30s 超时拖死整轮。
    """
    return os.getenv("A_SHARE_CRAWL_CACHE_ONLY", "").strip().lower() in {
        "1", "true", "yes", "y", "on",
    }


def get_ashare_research_reports(
    ticker: str,
    end_date: str,
    limit: int = 10,
) -> list[dict]:
    """A股券商研报（东财 stock_research_report_em），按'日期'<=end_date 前视过滤。

    研报=分析师评级+机构+盈利预测，是机构视角的情绪/预期信号。
    缓存 research.pkl：首次拉全量历史，之后按 end_date 切片，回测全程读盘。
    """
    if not _is_a_share(ticker):
        return []
    df = _load_fin_cache(ticker, "research")
    if df is None:
        if _crawl_cache_only():
            return []
        try:
            import akshare as ak
            df = _akshare_call(
                ak.stock_research_report_em,
                symbol=_to_akshare_symbol(ticker),
                timeout=30,
            )
            if df is not None and not df.empty:
                _save_fin_cache(df, ticker, "research")
        except Exception as exc:
            logger.warning("stock_research_report_em failed for %s: %s", ticker, exc)
            return []
    if df is None or df.empty or "日期" not in df.columns:
        return []
    end_ts = pd.to_datetime(end_date)
    sub = df.copy()
    sub["_d"] = pd.to_datetime(sub["日期"], errors="coerce")
    sub = sub[sub["_d"].notna() & (sub["_d"] <= end_ts)].sort_values(
        "_d", ascending=False,
    )
    out: list[dict] = []
    for _, row in sub.head(limit).iterrows():
        out.append({
            "date": row["_d"].strftime("%Y-%m-%d"),
            "title": str(row.get("报告名称", "")),
            "rating": str(row.get("东财评级", "")),
            "institution": str(row.get("机构", "")),
            "industry": str(row.get("行业", "")),
        })
    return out


def get_ashare_top_holders(
    ticker: str,
    end_date: str,
    top_n: int = 10,
) -> list[dict]:
    """A股十大股东（新浪 stock_main_stock_holder），按'公告日期'<=end_date 取最新报告期。

    用公告日期而非截止日期做前视控制：季报截止 12-31 但常在次年 1 月才公告，
    只有按公告日期过滤才能保证回测当日真正可见。缓存 holder_sina.pkl。
    """
    if not _is_a_share(ticker):
        return []
    df = _load_fin_cache(ticker, "holder_sina")
    if df is None:
        if _crawl_cache_only():
            return []
        try:
            import akshare as ak
            df = _akshare_call(
                ak.stock_main_stock_holder,
                stock=_to_akshare_symbol(ticker),
                timeout=30,
            )
            if df is not None and not df.empty:
                _save_fin_cache(df, ticker, "holder_sina")
        except Exception as exc:
            logger.warning("stock_main_stock_holder failed for %s: %s", ticker, exc)
            return []
    if df is None or df.empty or "公告日期" not in df.columns:
        return []
    end_ts = pd.to_datetime(end_date)
    sub = df.copy()
    sub["_pub"] = pd.to_datetime(sub["公告日期"], errors="coerce")
    sub = sub[sub["_pub"].notna() & (sub["_pub"] <= end_ts)]
    if sub.empty:
        return []
    if "截至日期" in sub.columns:
        sub["_asof"] = pd.to_datetime(sub["截至日期"], errors="coerce")
        latest = sub["_asof"].max()
        sub = sub[sub["_asof"] == latest]
    out: list[dict] = []
    for _, row in sub.head(top_n).iterrows():
        out.append({
            "name": str(row.get("股东名称", "")),
            "shares": row.get("持股数量"),
            "pct": row.get("持股比例"),
            "nature": str(row.get("股本性质", "")),
            "as_of": str(row.get("截至日期", "")),
        })
    return out


def _fetch_akshare_company_news(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
) -> list[CompanyNews]:
    """Fetch A-share company announcements from Eastmoney (公告大全).

    改用 stock_individual_notice_report 替代 stock_news_em：
    - 支持 begin_date/end_date 精确日期范围，可回溯历史
    - 覆盖重大事项、财务报告、风险提示等结构化公告
    - stock_news_em 只返回最近 10 条，对回测无用

    磁盘缓存策略：按 ticker 缓存全量历史公告 (notice.pkl)，
    首次拉取后切片，不重复网络请求。
    """
    try:
        import akshare as ak
    except Exception as exc:  # pragma: no cover
        logger.warning("akshare import failed for company notices %s: %s", ticker, exc)
        return []

    symbol = _to_akshare_symbol(ticker)  # 纯 6 位数字

    # ── 磁盘缓存：优先读全量公告缓存 ────────────────────────────────────
    df = _load_fin_cache(ticker, "notice")
    if df is None:
        if _crawl_cache_only():
            # 离线模式：个股新闻/公告无可用历史源（见研报+股东替代方案），直接跳过
            return []
        # 拉取从上市以来到今天的全量公告（东方财富支持不传日期则返回全部）
        try:
            df = _akshare_call(
                ak.stock_individual_notice_report,
                security=symbol,
                symbol="全部",
                timeout=30,
            )
            if df is not None and not df.empty:
                _save_fin_cache(df, ticker, "notice")
        except Exception as exc:
            logger.warning("stock_individual_notice_report failed for %s: %s", ticker, exc)
            # stock_news_em 只返回近 10 条且无法回溯历史，对回测无意义，不再降级
            return []

    if df is None or df.empty:
        return []

    # ── 日期过滤 ─────────────────────────────────────────────────────────
    end_ts = pd.to_datetime(end_date)
    start_ts = pd.to_datetime(start_date) if start_date else None

    # 公告日期列名
    date_col = "公告日期" if "公告日期" in df.columns else df.columns[4]

    results: list[CompanyNews] = []
    for _, row in df.iterrows():
        pub = pd.to_datetime(row.get(date_col), errors="coerce")
        if pd.isna(pub):
            continue
        if pub > end_ts:
            continue
        if start_ts is not None and pub < start_ts:
            continue

        results.append(
            CompanyNews(
                ticker=ticker,
                title=str(row.get("公告标题", "") or ""),
                related=str(row.get("代码", symbol) or symbol),
                source="东方财富公告",
                date=pub.strftime("%Y-%m-%d"),
                url=str(row.get("网址", "") or ""),
                summary="",   # 公告接口无正文，标题已含关键信息
                category=str(row.get("公告类型", "全部") or "全部"),
            )
        )
        if len(results) >= limit:
            break

    return results


def _parse_news_em(
    ticker: str,
    symbol: str,
    df: "pd.DataFrame",
    start_date: str | None,
    end_date: str,
    limit: int,
) -> list[CompanyNews]:
    """stock_news_em 降级解析（保留旧逻辑）。"""
    end_ts = pd.to_datetime(end_date)
    start_ts = pd.to_datetime(start_date) if start_date else None
    results: list[CompanyNews] = []
    for _, row in df.iterrows():
        published = pd.to_datetime(row.get("发布时间"), errors="coerce")
        if pd.notna(published):
            if published > end_ts:
                continue
            if start_ts is not None and published < start_ts:
                continue
            date_str = published.strftime("%Y-%m-%d")
        else:
            date_str = None
        results.append(
            CompanyNews(
                ticker=ticker,
                title=str(row.get("新闻标题", "") or ""),
                related=symbol,
                source=str(row.get("文章来源", "Eastmoney") or "Eastmoney"),
                date=date_str,
                url=str(row.get("新闻链接", "") or ""),
                summary=str(row.get("新闻内容", "") or ""),
                category="a_share_news",
            )
        )
        if len(results) >= limit:
            break
    return results


def _fetch_finnhub_company_news(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    api_key: str,
) -> list[CompanyNews]:
    """Fetch company news from Finnhub API."""
    client = finnhub.Client(api_key=api_key)

    from_date = start_date or (
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        - datetime.timedelta(days=30)
    ).strftime("%Y-%m-%d")

    news_data = client.company_news(ticker, _from=from_date, to=end_date)

    if not news_data:
        return []

    all_news = []
    for news_item in news_data[:limit]:
        company_news = CompanyNews(
            ticker=ticker,
            title=news_item.get("headline", ""),
            related=news_item.get("related", ""),
            source=news_item.get("source", ""),
            date=(
                datetime.datetime.fromtimestamp(
                    news_item.get("datetime", 0),
                    datetime.timezone.utc,
                ).strftime("%Y-%m-%d")
                if news_item.get("datetime")
                else None
            ),
            url=news_item.get("url", ""),
            summary=news_item.get("summary", ""),
            category=news_item.get("category", ""),
        )
        all_news.append(company_news)
    return all_news


def _fetch_fd_company_news(
    ticker: str,
    start_date: str | None,
    end_date: str,
    limit: int,
    api_key: str,
) -> list[CompanyNews]:
    """Fetch company news from Financial Datasets API."""
    headers = {"X-API-KEY": api_key}
    all_news = []
    current_end_date = end_date

    while True:
        url = f"https://api.financialdatasets.ai/news/?ticker={ticker}&end_date={current_end_date}"
        if start_date:
            url += f"&start_date={start_date}"
        url += f"&limit={limit}"

        response = _make_api_request(url, headers)
        if response.status_code != 200:
            raise ValueError(
                f"Error fetching data: {ticker} - {response.status_code} - {response.text}",
            )

        data = response.json()
        response_model = CompanyNewsResponse(**data)
        company_news = response_model.news

        if not company_news:
            break

        all_news.extend(company_news)

        if not start_date or len(company_news) < limit:
            break

        current_end_date = min(
            news.date for news in company_news if news.date is not None
        ).split("T")[0]

        if current_end_date <= start_date:
            break

    return all_news


def get_company_news(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> list[CompanyNews]:
    """Fetch company news from cache or API."""
    if _is_a_share(ticker):
        source = "akshare"
        cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}_{source}"
        if cached_data := _cache.get_company_news(cache_key):
            return [CompanyNews(**news) for news in cached_data]

        if snapshot_data := _load_company_news_snapshot(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            source=source,
        ):
            _cache.set_company_news(
                cache_key,
                [news.model_dump() for news in snapshot_data],
            )
            return snapshot_data

        all_news = _fetch_akshare_company_news(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if not all_news:
            return []

        _cache.set_company_news(
            cache_key,
            [news.model_dump() for news in all_news],
        )
        _save_company_news_snapshot(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            source=source,
            news=all_news,
        )
        return all_news

    config = get_config()
    data_source = config.source
    api_key = config.api_key

    cache_key = (
        f"{ticker}_{start_date or 'none'}_{end_date}_{limit}_{data_source}"
    )

    if cached_data := _cache.get_company_news(cache_key):
        return [CompanyNews(**news) for news in cached_data]

    if snapshot_data := _load_company_news_snapshot(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        source=data_source,
    ):
        _cache.set_company_news(
            cache_key,
            [news.model_dump() for news in snapshot_data],
        )
        return snapshot_data

    if data_source == "finnhub":
        all_news = _fetch_finnhub_company_news(
            ticker,
            start_date,
            end_date,
            limit,
            api_key,
        )
    else:
        all_news = _fetch_fd_company_news(
            ticker,
            start_date,
            end_date,
            limit,
            api_key,
        )

    if not all_news:
        return []

    _cache.set_company_news(
        cache_key,
        [news.model_dump() for news in all_news],
    )
    _save_company_news_snapshot(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        source=data_source,
        news=all_news,
    )
    return all_news


def get_company_news_with_trace(
    ticker: str,
    end_date: str,
    start_date: str | None = None,
    limit: int = 1000,
) -> tuple[list[CompanyNews], dict]:
    """
    Fetch company news together with provenance metadata.

    Trace fields:
    - transport: memory_cache / local_snapshot / live_fetch / empty
    - source: akshare / finnhub / financial_datasets
    """
    if _is_a_share(ticker):
        source = "akshare"
        cache_key = f"{ticker}_{start_date or 'none'}_{end_date}_{limit}_{source}"

        if cached_data := _cache.get_company_news(cache_key):
            return (
                [CompanyNews(**news) for news in cached_data],
                {"transport": "memory_cache", "source": source},
            )

        if snapshot_data := _load_company_news_snapshot(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            source=source,
        ):
            _cache.set_company_news(
                cache_key,
                [news.model_dump() for news in snapshot_data],
            )
            return snapshot_data, {"transport": "local_snapshot", "source": source}

        live_data = _fetch_akshare_company_news(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
        if not live_data:
            return [], {"transport": "empty", "source": source}

        _cache.set_company_news(cache_key, [news.model_dump() for news in live_data])
        _save_company_news_snapshot(
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            source=source,
            news=live_data,
        )
        return live_data, {"transport": "live_fetch", "source": source}

    config = get_config()
    data_source = config.source
    api_key = config.api_key
    cache_key = (
        f"{ticker}_{start_date or 'none'}_{end_date}_{limit}_{data_source}"
    )

    if cached_data := _cache.get_company_news(cache_key):
        return (
            [CompanyNews(**news) for news in cached_data],
            {"transport": "memory_cache", "source": data_source},
        )

    if snapshot_data := _load_company_news_snapshot(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        source=data_source,
    ):
        _cache.set_company_news(
            cache_key,
            [news.model_dump() for news in snapshot_data],
        )
        return snapshot_data, {"transport": "local_snapshot", "source": data_source}

    if data_source == "finnhub":
        live_data = _fetch_finnhub_company_news(
            ticker,
            start_date,
            end_date,
            limit,
            api_key,
        )
    else:
        live_data = _fetch_fd_company_news(
            ticker,
            start_date,
            end_date,
            limit,
            api_key,
        )

    if not live_data:
        return [], {"transport": "empty", "source": data_source}

    _cache.set_company_news(cache_key, [news.model_dump() for news in live_data])
    _save_company_news_snapshot(
        ticker=ticker,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        source=data_source,
        news=live_data,
    )
    return live_data, {"transport": "live_fetch", "source": data_source}


def _convert_finnhub_insider_trade(ticker: str, trade: dict) -> InsiderTrade:
    """Convert Finnhub insider trade format to InsiderTrade model."""
    shares_after = trade.get("share", 0)
    change = trade.get("change", 0)

    return InsiderTrade(
        ticker=ticker,
        issuer=None,
        name=trade.get("name", ""),
        title=None,
        is_board_director=None,
        transaction_date=trade.get("transactionDate", ""),
        transaction_shares=abs(change),
        transaction_price_per_share=trade.get("transactionPrice", 0.0),
        transaction_value=abs(change) * trade.get("transactionPrice", 0.0),
        shares_owned_before_transaction=(
            shares_after - change if shares_after and change else None
        ),
        shares_owned_after_transaction=float(shares_after)
        if shares_after
        else None,
        security_title=None,
        filing_date=trade.get("filingDate", ""),
    )


def get_market_cap(ticker: str, end_date: str) -> float | None:
    """Fetch market cap from the API. Finnhub values are converted from millions."""
    config = get_config()
    data_source = config.source
    api_key = config.api_key

    # For today's date, use company facts API
    if end_date == datetime.datetime.now().strftime("%Y-%m-%d"):
        headers = {"X-API-KEY": api_key}
        url = (
            f"https://api.financialdatasets.ai/company/facts/?ticker={ticker}"
        )
        response = _make_api_request(url, headers)
        if response.status_code != 200:
            return None

        data = response.json()
        response_model = CompanyFactsResponse(**data)
        return response_model.company_facts.market_cap

    financial_metrics = get_financial_metrics(ticker, end_date)
    if not financial_metrics:
        return None

    market_cap = financial_metrics[0].market_cap
    if not market_cap:
        return None

    # Finnhub returns market cap in millions
    if data_source == "finnhub":
        market_cap = market_cap * 1_000_000

    return market_cap


def prices_to_df(prices: list[Price]) -> pd.DataFrame:
    """Convert prices to a DataFrame."""
    df = pd.DataFrame([p.model_dump() for p in prices])
    df["Date"] = pd.to_datetime(df["time"])
    df.set_index("Date", inplace=True)
    numeric_cols = ["open", "close", "high", "low", "volume"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.sort_index(inplace=True)
    return df
