import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider
from app.market.events import PriceUpdatedEvent
from app.config.logging import setup_logging


async def test_provider(
    provider_enum: Provider,
    provider_name: str,
    btc_symbol: str,
    eth_symbol: str,
) -> None:
    """Test a single provider with BTC and ETH subscriptions."""
    logger.info("=" * 70)
    logger.info(f"Testing {provider_name} Provider")
    logger.info("=" * 70)

    dispatcher = EventDispatcher()
    cache = PriceCache()

    dispatcher.subscribe(
        PriceUpdatedEvent,
        cache.on_price_updated,
    )

    # Create manager with only this provider
    if provider_enum == Provider.BINANCE:
        provider_instance = BinanceProvider(dispatcher)
    elif provider_enum == Provider.BYBIT:
        provider_instance = BybitProvider(dispatcher)
    else:  # OKX
        provider_instance = OKXProvider(dispatcher)

    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers={
            provider_enum: provider_instance,
        },
        primary=provider_enum,
    )

    await manager.start()
    logger.info(f"{provider_name} manager started.")

    # Subscribe to BTC
    await manager.subscribe(btc_symbol)
    logger.info(f"{provider_name}: Subscribed to {btc_symbol}")

    # Monitor BTC for 5 seconds
    for i in range(5):
        await asyncio.sleep(1)
        tick = manager.get_price(btc_symbol)
        if tick:
            logger.success(f"{provider_name} BTC [{i+1}/5]: {tick.symbol} = {tick.price}")
        else:
            logger.warning(f"{provider_name} BTC [{i+1}/5]: No data yet")

    # Subscribe to ETH
    await manager.subscribe(eth_symbol)
    logger.info(f"{provider_name}: Subscribed to {eth_symbol}")

    # Monitor both BTC and ETH for 5 seconds
    for i in range(5):
        await asyncio.sleep(1)
        btc_tick = manager.get_price(btc_symbol)
        eth_tick = manager.get_price(eth_symbol)

        if btc_tick:
            logger.success(f"{provider_name} BTC [{i+1}/5]: {btc_tick.symbol} = {btc_tick.price}")
        if eth_tick:
            logger.success(f"{provider_name} ETH [{i+1}/5]: {eth_tick.symbol} = {eth_tick.price}")

    # Unsubscribe from both
    await manager.unsubscribe(btc_symbol)
    await manager.unsubscribe(eth_symbol)
    logger.info(f"{provider_name}: Unsubscribed from {btc_symbol} and {eth_symbol}")

    # Stop the manager
    await manager.stop()
    logger.info(f"{provider_name} manager stopped.\n")


async def main() -> None:
    """Test all providers one by one."""
    
    # Test Binance
    await test_provider(
        Provider.BINANCE,
        "Binance",
        "BTCUSDT",
        "ETHUSDT",
    )

    # Test Bybit
    await test_provider(
        Provider.BYBIT,
        "Bybit",
        "BTCUSDT",
        "ETHUSDT",
    )

    # Test OKX (now uses normalized symbols)
    await test_provider(
        Provider.OKX,
        "OKX",
        "BTCUSDT",  # Now normalized - will convert internally to BTC-USDT-SWAP
        "ETHUSDT",  # Now normalized - will convert internally to ETH-USDT-SWAP
    )

    logger.success("All provider tests completed!")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
