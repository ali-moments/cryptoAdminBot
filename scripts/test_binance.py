"""Test script for REST market system."""
import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.manager import ProviderManager
from app.market.registry import ProviderRegistry
from app.config.logging import setup_logging


async def main() -> None:
    """Test REST market system with Binance."""
    logger.info("=" * 70)
    logger.info("TESTING REST MARKET SYSTEM - BINANCE")
    logger.info("=" * 70)

    # Create cache and providers using new REST architecture
    cache = PriceCache()
    providers = ProviderRegistry.create_all_providers(
        cache=cache,
        polling_intervals={
            Provider.BINANCE: 2.0,  # Fast polling for testing
            Provider.BYBIT: 3.0,
            Provider.OKX: 4.0,
        }
    )

    manager = ProviderManager(
        cache=cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
        consecutive_miss_threshold=2,
        check_interval=1.0,  # Fast failover for testing
    )

    await manager.start()
    logger.info("REST market manager started.")

    # Add symbols to track
    await manager.sync({"BTCUSDT", "ETHUSDT"})
    logger.info("Added BTCUSDT and ETHUSDT to tracking")

    # Monitor for 10 seconds
    logger.info("Monitoring prices for 10 seconds...")
    for i in range(10):
        await asyncio.sleep(1)
        
        btc_tick = manager.get_price("BTCUSDT")
        eth_tick = manager.get_price("ETHUSDT")

        if btc_tick:
            logger.success(f"[{i+1}/10] BTC: ${btc_tick.price} from {btc_tick.provider.value}")
        else:
            logger.warning(f"[{i+1}/10] BTC: No data yet")
            
        if eth_tick:
            logger.success(f"[{i+1}/10] ETH: ${eth_tick.price} from {eth_tick.provider.value}")
        else:
            logger.warning(f"[{i+1}/10] ETH: No data yet")

    # Remove symbols
    await manager.sync(set())
    logger.info("Removed all symbols from tracking")

    # Stop the manager
    await manager.stop()
    logger.success("REST market test completed!")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
