"""Test script for Bybit provider."""
import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.bybit import BybitProvider
from app.market.events import PriceUpdatedEvent
from app.config.logging import setup_logging


async def main() -> None:
    """Test Bybit provider."""
    logger.info("=" * 70)
    logger.info("TESTING BYBIT PROVIDER")
    logger.info("=" * 70)

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
            Provider.BYBIT: BybitProvider(dispatcher),
        },
        primary=Provider.BYBIT,
    )

    await manager.start()
    logger.info("Bybit manager started.")

    # Subscribe to BTC
    await manager.subscribe("BTCUSDT")
    logger.info("Subscribed to BTCUSDT")

    # Monitor BTC for 5 seconds
    for i in range(5):
        await asyncio.sleep(1)
        tick = manager.get_price("BTCUSDT")
        if tick:
            logger.success(f"BTC [{i+1}/5]: {tick.symbol} = {tick.price}")
        else:
            logger.warning(f"BTC [{i+1}/5]: No data yet")

    # Subscribe to ETH
    await manager.subscribe("ETHUSDT")
    logger.info("Subscribed to ETHUSDT")

    # Monitor both BTC and ETH for 5 seconds
    for i in range(5):
        await asyncio.sleep(1)
        btc_tick = manager.get_price("BTCUSDT")
        eth_tick = manager.get_price("ETHUSDT")

        if btc_tick:
            logger.success(f"BTC [{i+1}/5]: {btc_tick.symbol} = {btc_tick.price}")
        if eth_tick:
            logger.success(f"ETH [{i+1}/5]: {eth_tick.symbol} = {eth_tick.price}")

    # Unsubscribe from both
    await manager.unsubscribe("BTCUSDT")
    await manager.unsubscribe("ETHUSDT")
    logger.info("Unsubscribed from BTCUSDT and ETHUSDT")

    # Stop the manager
    await manager.stop()
    logger.success("Bybit test completed!")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
