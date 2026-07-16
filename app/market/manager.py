from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider


class ProviderManager:
    def __init__(
        self,
        dispatcher: EventDispatcher,
        cache: PriceCache,
        providers: dict[Provider, BaseProvider],
        primary: Provider = Provider.BINANCE,
        fallback: Provider = Provider.BYBIT,
        disaster: Provider = Provider.OKX,
    ) -> None:
        self._dispatcher = dispatcher
        self._cache = cache

        self._providers = providers

        self._primary = primary
        self._fallback = fallback
        self._disaster = disaster

        self._active = primary

        self._subscriptions: dict[str, int] = {}

    @property
    def active_provider(self) -> BaseProvider:
        return self._providers[self._active]

    async def start(self) -> None:
        await self.active_provider.connect()

    async def stop(self) -> None:
        await self.active_provider.disconnect()

    async def subscribe(
        self,
        symbol: str,
    ) -> None:
        count = self._subscriptions.get(symbol, 0)

        if count == 0:
            await self.active_provider.subscribe(symbol)

        self._subscriptions[symbol] = count + 1

    async def unsubscribe(
        self,
        symbol: str,
    ) -> None:
        count = self._subscriptions.get(symbol)

        if count is None:
            return

        if count == 1:
            await self.active_provider.unsubscribe(symbol)
            del self._subscriptions[symbol]
            return

        self._subscriptions[symbol] = count - 1

    def get_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
        return self._cache.get(symbol)
