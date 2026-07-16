import asyncio

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.binance import BinanceProvider
from app.market.events import PriceUpdatedEvent


async def main() -> None:
    dispatcher = EventDispatcher()

    cache = PriceCache()

    dispatcher.subscribe(
        PriceUpdatedEvent,
        cache.on_price_updated,
    )

    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers={
            Provider.BINANCE: BinanceProvider(dispatcher),
        },
    )

    await manager.start()

    await manager.subscribe("BTCUSDT")

    while True:
        tick = manager.get_price("BTCUSDT")

        if tick:
            print(tick)

        await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
