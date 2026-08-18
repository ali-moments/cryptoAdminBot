from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider

BYBIT_TICKER_URL = "https://api.bybit.com/v5/market/tickers"


class BybitProvider(BaseProvider):
    @property
    def name(self) -> Provider:
        return Provider.BYBIT

    async def fetch_all_tickers(self) -> list[PriceTick]:
        now = datetime.now(timezone.utc)
        async with self._session.get(BYBIT_TICKER_URL, params={"category": "linear"}) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")

        ticks: list[PriceTick] = []
        for item in data.get("result", {}).get("list", []):
            try:
                ticks.append(PriceTick(
                    symbol=item["symbol"],  # already canonical, e.g. "BTCUSDT"
                    price=Decimal(item["lastPrice"]),
                    provider=Provider.BYBIT,
                    timestamp=now,
                ))
            except (KeyError, ValueError) as e:
                logger.warning(f"Bybit: skipping malformed ticker {item}: {e}")
        return ticks