from loguru import logger
from app.market.dto import PriceTick
from app.market.events import PriceUpdatedEvent


class PriceCache:
    def __init__(self) -> None:
        self._prices: dict[str, PriceTick] = {}

    async def on_price_updated(
        self,
        event: PriceUpdatedEvent,
    ) -> None:
        logger.info(f"PRICE_CACHE_UPDATE: {event.tick.symbol} @ {event.tick.price} from {event.tick.provider.value}")
        self._prices[event.tick.symbol] = event.tick

    def get(
        self,
        symbol: str,
    ) -> PriceTick | None:
        return self._prices.get(symbol)

    def remove(
        self,
        symbol: str,
    ) -> None:
        self._prices.pop(symbol, None)

    def clear(self) -> None:
        self._prices.clear()

    def __contains__(
        self,
        symbol: str,
    ) -> bool:
        return symbol in self._prices
