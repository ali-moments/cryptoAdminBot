import asyncio
from datetime import UTC, datetime
from decimal import Decimal
import aiohttp
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider


class BybitProvider(BaseProvider):
    REST_URL = "https://api.bybit.com/v5/market/tickers"

    def __init__(
        self,
        dispatcher,
        polling_interval: float = 4.0,  # Bybit moderate rate limits
    ) -> None:
        super().__init__(dispatcher, polling_interval)

    @property
    def name(
        self,
    ) -> Provider:
        return Provider.BYBIT

    async def fetch_all_tickers(self) -> list[PriceTick]:
        """Fetch all linear tickers from Bybit API"""
        if self._session is None:
            raise RuntimeError("Provider session not initialized")

        async with self._session.get(
            self.REST_URL,
            params={
                "category": "linear",  # Get all linear perpetual futures
            },
        ) as response:
            response.raise_for_status()
            data = await response.json()

        ticks = []
        for ticker in data["result"]["list"]:
            # Use lastPrice from ticker data
            if "lastPrice" in ticker and ticker["lastPrice"]:
                ticks.append(PriceTick(
                    provider=self.name,
                    symbol=ticker["symbol"],
                    price=Decimal(ticker["lastPrice"]),
                    timestamp=datetime.now(UTC),
                ))

        logger.debug(f"Bybit: Fetched {len(ticks)} tickers")
        return ticks

    async def current_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

        async with self._session.get(
            self.REST_URL,
            params={
                "category": "linear",
                "symbol": symbol.upper(),
            },
        ) as response:
            response.raise_for_status()
            data = await response.json()

        result = data["result"]["list"][0]

        return PriceTick(
            provider=self.name,
            symbol=result["symbol"],
            price=Decimal(result["lastPrice"]),
            timestamp=datetime.now(UTC),
        )
