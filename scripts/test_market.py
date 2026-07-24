import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.binance import BinanceProvider
from app.market.events import PriceUpdatedEvent
from app.config.logging import setup_logging


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
    logger.info("Manager started.")
    await manager.subscribe("BTCUSDT")
    logger.info("bitcoin subscription added.")
    counter = 0
    while counter < 15:
        counter += 1
        tick = manager.get_price("BTCUSDT")

        if tick:
            print(tick)

        await asyncio.sleep(1)
    #await manager.unsubscribe("BTCUSDT")
    #logger.info("bitcoin unsubscribed.")
    await manager.subscribe("ETHUSDT")
    logger.info("ETH subscription added.")
    counter = 0
    while counter < 15:
        counter += 1
        tick = manager.get_price("ETHUSDT")
        if tick:
            print(tick)
        await asyncio.sleep(1)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
