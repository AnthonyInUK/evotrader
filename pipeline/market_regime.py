from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
MARKET_INDEX_CACHE = ROOT / "backend" / "data" / "market_index_cache"


@dataclass
class MarketRegime:
    label: str
    ret20_pct: float
    ret60_pct: float
    close: float
    sma20: float
    sma60: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "ret20_pct": self.ret20_pct,
            "ret60_pct": self.ret60_pct,
            "close": self.close,
            "sma20": self.sma20,
            "sma60": self.sma60,
            "reason": self.reason,
        }


def detect_market_regime(
    run_date: date,
    *,
    index_symbol: str = "sh000001",
) -> MarketRegime:
    frame = _load_index_window(index_symbol, run_date)
    if frame is None or len(frame) < 61:
        return MarketRegime(
            label="unknown",
            ret20_pct=0.0,
            ret60_pct=0.0,
            close=0.0,
            sma20=0.0,
            sma60=0.0,
            reason="market index cache missing or insufficient",
        )

    close = frame["close"].astype(float)
    latest = float(close.iloc[-1])
    sma20 = float(close.tail(20).mean())
    sma60 = float(close.tail(60).mean())
    ret20 = latest / float(close.iloc[-21]) - 1
    ret60 = latest / float(close.iloc[-61]) - 1

    if ret20 >= 0.04 and latest >= sma20 >= sma60:
        label = "rebound"
        reason = "20d return positive and index above SMA20/SMA60"
    elif ret20 <= -0.04 or latest < sma60:
        label = "weak"
        reason = "20d return negative or index below SMA60"
    else:
        label = "sideways"
        reason = "index lacks clear rebound or weak trend"

    return MarketRegime(
        label=label,
        ret20_pct=round(ret20 * 100, 4),
        ret60_pct=round(ret60 * 100, 4),
        close=round(latest, 4),
        sma20=round(sma20, 4),
        sma60=round(sma60, 4),
        reason=reason,
    )


def _load_index_window(index_symbol: str, run_date: date) -> pd.DataFrame | None:
    candidates = sorted(MARKET_INDEX_CACHE.glob(f"{index_symbol}_*.parquet"))
    run_ts = pd.Timestamp(run_date)
    for path in reversed(candidates):
        try:
            frame = pd.read_parquet(path)
        except Exception:
            continue
        frame = _normalize(frame)
        if frame.empty:
            continue
        window = frame.loc[frame.index <= run_ts].tail(90)
        if not window.empty:
            return window
    return None


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    if "date" in df.columns:
        df.index = pd.to_datetime(df["date"], errors="coerce")
    elif "time" in df.columns:
        df.index = pd.to_datetime(df["time"], errors="coerce")
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, errors="coerce")
    df = df[df.index.notna()].sort_index()
    df = df.rename(
        columns={
            "Close": "close",
            "收盘": "close",
        },
    )
    if "close" not in df.columns:
        return pd.DataFrame()
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    return df.dropna(subset=["close"])
