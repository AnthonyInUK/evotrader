#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seed broad-market and sector/theme index caches for A-share selection.

This script is intentionally separate from stock price seeding. The selector
needs market/sector context, but those datasets use different akshare endpoints
and should not pollute the stock OHLCV cache.
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

MARKET_INDEX_CACHE_DIR = ROOT / "backend" / "data" / "market_index_cache"
THEME_INDEX_CACHE_DIR = ROOT / "backend" / "data" / "theme_index_cache"

DEFAULT_MARKET_INDICES = {
    "000001.SH": {"em_symbol": "sh000001", "sina_symbol": "sh000001", "name": "上证指数"},
    "399001.SZ": {"em_symbol": "sz399001", "sina_symbol": "sz399001", "name": "深证成指"},
    "399006.SZ": {"em_symbol": "sz399006", "sina_symbol": "sz399006", "name": "创业板指"},
    "000300.SH": {"em_symbol": "sh000300", "sina_symbol": "sh000300", "name": "沪深300"},
    "000905.SH": {"em_symbol": "csi000905", "sina_symbol": "sh000905", "name": "中证500"},
    "000852.SH": {"em_symbol": "csi000852", "sina_symbol": "sh000852", "name": "中证1000"},
}

# ── 主题名称说明 ──────────────────────────────────────────────────────────────
# 名称必须与 THS 返回的板块名称精确匹配（区分大小写）。
# 如果不确定名称，先跑 scripts/probe_theme_sources.py --list-all 查全量列表。
# 已知注意点：
#   "酿酒行业"/"家电行业" 在 THS 可能叫 "白酒"/"家用电器"，需本地探测确认。
# ─────────────────────────────────────────────────────────────────────────────
DEFAULT_INDUSTRY_THEMES = [
    "银行",
    "证券",
    "保险",
    "白酒",
    "白色家电",
    "电池",
    "光伏设备",
    "半导体",
    "通信设备",
    "计算机设备",
    "软件开发",
    "医疗服务",
    "化学制药",
    "中药",
]

DEFAULT_CONCEPT_THEMES = [
    "白酒概念",
    "券商概念",
    "国企改革",
    "人工智能",
    "算力概念",
    "创新药",
    "固态电池",
]

# ── ETF 代理配置 ──────────────────────────────────────────────────────────────
# 当 THS 和 EM 板块接口均失败时，用对应代表性 ETF 的日线行情作为主题代理。
# ETF 通过 akshare stock_zh_a_hist 拉取，与个股 OHLCV 同接口，成功率远高于板块指数接口。
# 说明：保险、国企改革、固态电池 暂无专用 ETF，用最近似的替代品，或留空跳过。
# ─────────────────────────────────────────────────────────────────────────────
THEME_ETF_PROXIES: dict[str, str] = {
    # 行业板块
    "银行":     "512800",   # 银行ETF（华夏）
    "证券":     "512880",   # 证券ETF（国泰）
    "保险":     "512070",   # 非银金融ETF（含保险+券商，无纯保险ETF）
    "白酒":     "512690",   # 酒ETF（含白酒+啤酒）
    "白色家电": "159996",   # 家电ETF（天弘）
    "电池":     "159819",   # 电池ETF（易方达）
    "光伏设备": "515790",   # 光伏ETF（华夏）
    "半导体":   "512480",   # 半导体ETF（国联安）
    "通信设备": "159869",   # 通信ETF（广发）
    "计算机设备":"512720",  # 计算机ETF（国泰）
    "软件开发": "159852",   # 软件ETF（博时）
    "医疗服务": "512170",   # 医疗ETF（华夏）
    "化学制药": "512010",   # 医药ETF（易方达）
    "中药":     "159647",   # 中药ETF（汇添富）
    # 概念板块
    "白酒概念": "512690",   # 酒ETF
    "券商概念": "512880",   # 证券ETF（THS无此名称，走ETF fallback）
    "国企改革": "510010",   # 上证50ETF（国企权重大，作近似）
    "人工智能": "515070",   # AI ETF（华夏）
    "算力概念": "515070",   # 复用 AI ETF（华夏）作代理；算力与AI高度重叠，159418上市太晚数据不足
    "创新药":   "159992",   # 创新药ETF（易方达）
    "固态电池": "159819",   # 电池ETF（无纯固态电池ETF，用电池ETF近似）
}


class TimeoutError(Exception):
    """Raised when one akshare call takes too long."""


@contextmanager
def _timeout(seconds: int):
    def _handler(_signum, _frame):
        raise TimeoutError(f"timeout after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(max(seconds, 1))
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _safe_name(value: str) -> str:
    return (
        value.replace(".", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
        .replace(":", "_")
    )


def _cache_path(cache_dir: Path, key: str, start_date: str, end_date: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{_safe_name(key)}_{start_date}_{end_date}.parquet"


def _meta_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {
        "日期": "time",
        "开盘": "open",
        "开盘价": "open",
        "收盘": "close",
        "收盘价": "close",
        "最高": "high",
        "最高价": "high",
        "最低": "low",
        "最低价": "low",
        "成交量": "volume",
        "成交额": "amount",
        "涨跌幅": "ret",
        "涨跌额": "change",
        "振幅": "amplitude",
        "换手率": "turnover",
        "date": "time",
    }
    frame = df.rename(columns=col_map).copy()
    keep = [
        col
        for col in [
            "time",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "ret",
            "change",
            "amplitude",
            "turnover",
        ]
        if col in frame.columns
    ]
    frame = frame[keep].copy()
    if "time" in frame.columns:
        frame["Date"] = pd.to_datetime(frame["time"], errors="coerce")
        frame = frame.dropna(subset=["Date"]).set_index("Date")
    elif not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        frame = frame[frame.index.notna()]
    frame = frame.sort_index()
    for col in ["open", "close", "high", "low", "volume", "amount", "ret", "change"]:
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _save_frame(
    df: pd.DataFrame,
    path: Path,
    *,
    kind: str,
    key: str,
    name: str,
    source: str,
    start_date: str,
    end_date: str,
) -> None:
    df.to_parquet(path)
    meta = {
        "kind": kind,
        "key": key,
        "name": name,
        "source": source,
        "start_date": start_date,
        "end_date": end_date,
        "rows": int(len(df)),
        "first_date": str(df.index.min().date()) if len(df) else None,
        "last_date": str(df.index.max().date()) if len(df) else None,
    }
    _meta_path(path).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _filter_date_range(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    return df[(df.index >= start) & (df.index <= end)].copy()


def _fetch_market_index(
    ak: Any,
    *,
    em_symbol: str,
    sina_symbol: str,
    start_date: str,
    end_date: str,
    source: str,
) -> tuple[pd.DataFrame, str]:
    if source == "sina":
        df = ak.stock_zh_index_daily(symbol=sina_symbol)
        return _filter_date_range(_normalize_ohlcv(df), start_date, end_date), "akshare_sina"

    if source == "eastmoney":
        df = ak.stock_zh_index_daily_em(
            symbol=em_symbol,
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
        )
        return _normalize_ohlcv(df), "akshare_eastmoney"

    errors: list[str] = []
    for candidate in ["sina", "eastmoney"]:
        try:
            return _fetch_market_index(
                ak,
                em_symbol=em_symbol,
                sina_symbol=sina_symbol,
                start_date=start_date,
                end_date=end_date,
                source=candidate,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("; ".join(errors))


def _fetch_industry(
    ak: Any,
    name: str,
    start_date: str,
    end_date: str,
    source: str,
) -> tuple[pd.DataFrame, str]:
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    if source == "ths":
        df = ak.stock_board_industry_index_ths(symbol=name, start_date=start, end_date=end)
        return _normalize_ohlcv(df), "akshare_ths"
    if source == "eastmoney":
        df = ak.stock_board_industry_hist_em(
            symbol=name,
            start_date=start,
            end_date=end,
            period="日k",
            adjust="",
        )
        return _normalize_ohlcv(df), "akshare_eastmoney"

    errors: list[str] = []
    for candidate in ["ths", "eastmoney"]:
        try:
            return _fetch_industry(ak, name, start_date, end_date, candidate)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("; ".join(errors))


def _fetch_concept(
    ak: Any,
    name: str,
    start_date: str,
    end_date: str,
    source: str,
) -> tuple[pd.DataFrame, str]:
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    if source == "ths":
        df = ak.stock_board_concept_index_ths(symbol=name, start_date=start, end_date=end)
        return _normalize_ohlcv(df), "akshare_ths"
    if source == "eastmoney":
        df = ak.stock_board_concept_hist_em(
            symbol=name,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="",
        )
        return _normalize_ohlcv(df), "akshare_eastmoney"

    errors: list[str] = []
    for candidate in ["ths", "eastmoney"]:
        try:
            return _fetch_concept(ak, name, start_date, end_date, candidate)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError("; ".join(errors))


def _fetch_etf_proxy(
    ak: Any,
    etf_code: str,
    start_date: str,
    end_date: str,
) -> tuple[pd.DataFrame, str]:
    """
    用 ETF 日线行情作为主题代理。
    使用 baostock（前复权），不走东方财富，稳定性更高。
    ETF code 格式：6 位数字，不带后缀（如 "512800"）。
    """
    import baostock as bs
    # 5xxxxx → 上交所ETF（如 512800, 515790）; 159xxx → 深交所ETF; 其他默认深交所
    bs_code = f"sh.{etf_code}" if etf_code.startswith("5") else f"sz.{etf_code}"
    lg = bs.login()
    try:
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=start_date,
            end_date=end_date,
            frequency="d",
            adjustflag="2",
        )
        rows = []
        while (rs.error_code == "0") and rs.next():
            rows.append(rs.get_row_data())
    finally:
        bs.logout()

    if not rows:
        raise RuntimeError(f"baostock 返回空数据: {bs_code}")

    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.replace("", float("nan"))
    df["Date"] = pd.to_datetime(df["time"], errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date")
    for col in ["open", "close", "high", "low", "volume", "amount", "ret"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["open", "close", "high", "low", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_index()
    return df, f"baostock_etf_proxy_{etf_code}"


def _seed_one(
    *,
    kind: str,
    key: str,
    name: str,
    cache_dir: Path,
    start_date: str,
    end_date: str,
    timeout_seconds: int,
    force: bool,
    market_source: str = "sina",
    theme_source: str = "ths",
    etf_fallback: bool = True,
) -> tuple[bool, str]:
    cache_key = key["sina_symbol"] if isinstance(key, dict) else key
    path = _cache_path(cache_dir, cache_key, start_date, end_date)
    if path.exists() and not force:
        try:
            cached = pd.read_parquet(path)
            if cached is not None and not cached.empty:
                return True, f"[CACHE] {kind} {key}: {len(cached)} rows"
        except Exception:
            path.unlink(missing_ok=True)
            _meta_path(path).unlink(missing_ok=True)

    import akshare as ak

    df, source = None, ""
    board_error: str | None = None

    # ── 行业/概念：直接用 baostock ETF，跳过 THS/东财板块接口 ──────────────────
    if kind in {"industry", "concept"} and name in THEME_ETF_PROXIES:
        etf_code = THEME_ETF_PROXIES[name]
        try:
            with _timeout(timeout_seconds):
                df, source = _fetch_etf_proxy(ak, etf_code, start_date, end_date)
        except Exception as exc:
            return False, f"[FAIL] {kind} {name}: ETF({etf_code}) 失败: {exc}"
    elif kind == "market":
        try:
            with _timeout(timeout_seconds):
                if not isinstance(key, dict):
                    raise TypeError("market key must be a metadata dict")
                df, source = _fetch_market_index(
                    ak,
                    em_symbol=key["em_symbol"],
                    sina_symbol=key["sina_symbol"],
                    start_date=start_date,
                    end_date=end_date,
                    source=market_source,
                )
        except Exception as exc:
            board_error = str(exc)
    elif kind in {"industry", "concept"}:
        # 没有 ETF 映射，才尝试 THS/东财
        try:
            with _timeout(timeout_seconds):
                if kind == "industry":
                    df, source = _fetch_industry(ak, name, start_date, end_date, theme_source)
                else:
                    df, source = _fetch_concept(ak, name, start_date, end_date, theme_source)
        except Exception as exc:
            board_error = str(exc)

    if df is None and board_error:
        return False, f"[FAIL] {kind} {name}: {board_error}"

    if df is None or df.empty:
        return False, f"[MISS] {kind} {name}: no rows"

    _save_frame(
        df,
        path,
        kind=kind,
        key=key["sina_symbol"] if isinstance(key, dict) else key,
        name=name,
        source=source,
        start_date=start_date,
        end_date=end_date,
    )
    return True, f"[SEEDED] {kind} {name}: {len(df)} rows -> {path.name}"


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed broad-market and sector/theme index caches.",
    )
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD（--list-* 时可省略）")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD（--list-* 时可省略）")
    parser.add_argument("--timeout-seconds", type=int, default=45)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--only",
        choices=["all", "market", "themes"],
        default="all",
        help="Which layer to seed.",
    )
    parser.add_argument(
        "--market-indices",
        default=",".join(DEFAULT_MARKET_INDICES.keys()),
        help="Comma-separated internal index codes.",
    )
    parser.add_argument(
        "--market-source",
        choices=["sina", "eastmoney", "auto"],
        default="sina",
        help="Market-index source. Sina avoids the Eastmoney endpoint for layer-1 indices.",
    )
    parser.add_argument(
        "--theme-source",
        choices=["ths", "eastmoney", "auto"],
        default="ths",
        help="Sector/theme source. THS is usually more stable for board-index histories.",
    )
    parser.add_argument(
        "--industries",
        default=",".join(DEFAULT_INDUSTRY_THEMES),
        help="Comma-separated Eastmoney industry board names.",
    )
    parser.add_argument(
        "--concepts",
        default=",".join(DEFAULT_CONCEPT_THEMES),
        help="Comma-separated Eastmoney concept board names.",
    )
    parser.add_argument(
        "--no-etf-fallback",
        action="store_true",
        help="禁用 ETF fallback。默认开启：板块接口失败时自动改用代理 ETF。",
    )
    parser.add_argument(
        "--list-defaults",
        action="store_true",
        help="Print the default market/theme list and exit.",
    )
    parser.add_argument(
        "--list-etf-proxies",
        action="store_true",
        help="Print the ETF proxy mapping and exit.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    # 非列表模式下才需要日期
    is_list_mode = args.list_etf_proxies or args.list_defaults
    if not is_list_mode and (not args.start_date or not args.end_date):
        print("错误：非 --list-* 模式下 --start-date 和 --end-date 必填", file=sys.stderr)
        return 2

    if args.list_etf_proxies:
        print("ETF Proxy Mapping (theme name → ETF code):")
        for theme, etf in THEME_ETF_PROXIES.items():
            print(f"  {theme:16s} → {etf}")
        return 0

    if args.list_defaults:
        print("Market indices:")
        for code, meta in DEFAULT_MARKET_INDICES.items():
            print(
                f"  {code}: {meta['name']} "
                f"(sina={meta['sina_symbol']}, eastmoney={meta['em_symbol']})"
            )
        print("Industries:")
        for item in DEFAULT_INDUSTRY_THEMES:
            etf = THEME_ETF_PROXIES.get(item, "-")
            print(f"  {item:20s} (ETF fallback: {etf})")
        print("Concepts:")
        for item in DEFAULT_CONCEPT_THEMES:
            etf = THEME_ETF_PROXIES.get(item, "-")
            print(f"  {item:20s} (ETF fallback: {etf})")
        return 0

    etf_fallback = not args.no_etf_fallback

    ok: list[str] = []
    failed: list[str] = []

    if args.only in {"all", "market"}:
        for code in _split_csv(args.market_indices):
            if code not in DEFAULT_MARKET_INDICES:
                print(f"[SKIP] unknown market index code: {code}")
                failed.append(code)
                continue
            meta = DEFAULT_MARKET_INDICES[code]
            success, message = _seed_one(
                kind="market",
                key=meta,
                name=meta["name"],
                cache_dir=MARKET_INDEX_CACHE_DIR,
                start_date=args.start_date,
                end_date=args.end_date,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
                market_source=args.market_source,
            )
            print(message)
            (ok if success else failed).append(code)

    if args.only in {"all", "themes"}:
        for name in _split_csv(args.industries):
            success, message = _seed_one(
                kind="industry",
                key=f"industry_{name}",
                name=name,
                cache_dir=THEME_INDEX_CACHE_DIR,
                start_date=args.start_date,
                end_date=args.end_date,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
                theme_source=args.theme_source,
                etf_fallback=etf_fallback,
            )
            print(message)
            (ok if success else failed).append(f"industry:{name}")

        for name in _split_csv(args.concepts):
            success, message = _seed_one(
                kind="concept",
                key=f"concept_{name}",
                name=name,
                cache_dir=THEME_INDEX_CACHE_DIR,
                start_date=args.start_date,
                end_date=args.end_date,
                timeout_seconds=args.timeout_seconds,
                force=args.force,
                theme_source=args.theme_source,
                etf_fallback=etf_fallback,
            )
            print(message)
            (ok if success else failed).append(f"concept:{name}")

    print("\n=== Market Index Cache Seed Summary ===")
    etf_note = "" if etf_fallback else " (ETF fallback 已禁用)"
    print(f"OK     ({len(ok)}): {', '.join(ok) if ok else '-'}")
    print(f"FAILED ({len(failed)}): {', '.join(failed) if failed else '-'}{etf_note}")
    print(f"Market cache: {MARKET_INDEX_CACHE_DIR}")
    print(f"Theme cache : {THEME_INDEX_CACHE_DIR}")
    if failed and etf_fallback:
        print(
            "\n提示：如果失败项有 ETF 代理可用，请检查以上 [ETF-FALLBACK] 日志。"
            "\n若 ETF 也失败，通常是网络环境问题（代理/VPN 拦截国内请求）。"
        )
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
