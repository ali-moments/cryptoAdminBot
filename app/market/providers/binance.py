from datetime import datetime, timezone
from decimal import Decimal
from loguru import logger

from app.database.enums import Provider
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider

BINANCE_TICKER_URL = "https://fapi.binance.com/fapi/v2/ticker/price"


class BinanceProvider(BaseProvider):
    @property
    def name(self) -> Provider:
        return Provider.BINANCE

    async def fetch_all_tickers(self) -> list[PriceTick]:
        now = datetime.now(timezone.utc)
        async with self._session.get(BINANCE_TICKER_URL) as resp:
            resp.raise_for_status()
            data = await resp.json()

        ticks: list[PriceTick] = []
        for item in data:
            try:
                ticks.append(PriceTick(
                    symbol=item["symbol"],  # already canonical, e.g. "BTCUSDT"
                    price=Decimal(item["price"]),
                    provider=Provider.BINANCE,
                    timestamp=now,
                ))
            except (KeyError, ValueError) as e:
                logger.warning(f"Binance: skipping malformed ticker {item}: {e}")
        return ticks