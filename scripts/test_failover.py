"""Test script for REST failover system."""
import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.manager import ProviderManager
from app.market.registry import ProviderRegistry
from app.config.logging import setup_logging


async def main() -> None:
    """Test REST failover system."""
    logger.info("=" * 70)
    logger.info("TESTING REST FAILOVER SYSTEM")
    logger.info("=" * 70)

    # Create cache and providers
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
        consecutive_miss_threshold=2,  # Fail after 2 misses
        check_interval=1.0,  # Check every second
    )

    await manager.start()
    logger.info("Failover manager started.")

    # Add symbols to track
    await manager.sync({"BTCUSDT", "ETHUSDT"})
    logger.info("Added symbols to tracking")

    # Monitor for 15 seconds to see failover in action
    logger.info("Monitoring for 15 seconds (automatic failover may occur)...")
    for i in range(15):
        await asyncio.sleep(1)
        
        btc_tick = manager.get_price("BTCUSDT")
        eth_tick = manager.get_price("ETHUSDT")

        if btc_tick:
            logger.success(f"[{i+1:2}/15] BTC: ${btc_tick.price} from {btc_tick.provider.value}")
        if eth_tick:
            logger.success(f"[{i+1:2}/15] ETH: ${eth_tick.price} from {eth_tick.provider.value}")

        if not btc_tick and not eth_tick:
            logger.warning(f"[{i+1:2}/15] No data for either symbol")

    await manager.stop()
    logger.success("Failover test completed!")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())