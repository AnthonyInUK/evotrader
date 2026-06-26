from __future__ import annotations

from datetime import date
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class MarketDataRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def upsert_ohlcv(self, rows: Iterable[dict]) -> None:
        stmt = text(
            """
            INSERT INTO market_data
                (symbol, date, open, high, low, close, volume, source)
            VALUES
                (:symbol, :date, :open, :high, :low, :close, :volume, :source)
            ON CONFLICT (symbol, date, source)
            DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume
            """
        )
        payload = list(rows)
        if payload:
            await self.db.execute(stmt, payload)

    async def query_by_symbol_range(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[dict]:
        result = await self.db.execute(
            text(
                """
                SELECT symbol, date, open, high, low, close, volume, source
                FROM market_data
                WHERE symbol = :symbol AND date BETWEEN :start_date AND :end_date
                ORDER BY date
                """
            ),
            {"symbol": symbol, "start_date": start_date, "end_date": end_date},
        )
        return [dict(row._mapping) for row in result]
