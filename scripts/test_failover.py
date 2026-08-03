"""
Test script for provider failover and recovery system.

This script demonstrates:
1. Normal operation with primary provider
2. Automatic failover when primary disconnects
3. Automatic recovery back to primary
"""
import asyncio
from loguru import logger

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.registry import ProviderRegistry
from app.market.events import (
    PriceUpdatedEvent,
    ProviderConnectedEvent,
    ProviderDisconnectedEvent,
    ProviderChangedEvent,
)
from app.config.logging import setup_logging


# Event handlers for monitoring
async def on_price_updated(event: PriceUpdatedEvent) -> None:
    """Log price updates."""
    logger.trace(
        f"Price update: {event.tick.symbol} = {event.tick.price} "
        f"(from {event.tick.provider.value})"
    )


async def on_provider_connected(event: ProviderConnectedEvent) -> None:
    """Log provider connections."""
    logger.success(f"🟢 Provider connected: {event.provider.value}")


async def on_provider_disconnected(event: ProviderDisconnectedEvent) -> None:
    """Log provider disconnections."""
    logger.warning(f"🔴 Provider disconnected: {event.provider.value}")


async def on_provider_changed(event: ProviderChangedEvent) -> None:
    """Log provider switches."""
    logger.info(
        f"🔄 Provider switched: {event.previous.value} → {event.current.value}"
    )


async def main() -> None:
    """Test the failover system."""
    logger.info("=" * 70)
    logger.info("TESTING PROVIDER FAILOVER AND RECOVERY SYSTEM")
    logger.info("=" * 70)

    # Setup
    dispatcher = EventDispatcher()
    cache = PriceCache()

    # Subscribe to all events for monitoring
    dispatcher.subscribe(PriceUpdatedEvent, cache.on_price_updated)
    dispatcher.subscribe(PriceUpdatedEvent, on_price_updated)
    dispatcher.subscribe(ProviderConnectedEvent, on_provider_connected)
    dispatcher.subscribe(ProviderDisconnectedEvent, on_provider_disconnected)
    dispatcher.subscribe(ProviderChangedEvent, on_provider_changed)

    # Create all providers
    providers = ProviderRegistry.create_all_providers(dispatcher)

    # Create manager with failover configuration
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
    )

    logger.info("\n📡 Starting ProviderManager...")
    await manager.start()
    
    logger.info(f"\n✅ Manager started with provider: {manager.active_provider_name.value}")
    logger.info(f"   Is using primary: {manager.is_using_primary}\n")

    # Subscribe to some symbols
    logger.info("📊 Subscribing to BTCUSDT and ETHUSDT...")
    await manager.subscribe("BTCUSDT")
    await manager.subscribe("ETHUSDT")

    # Monitor prices for 10 seconds
    logger.info("\n⏱️  Monitoring prices for 10 seconds...")
    for i in range(10):
        await asyncio.sleep(1)
        btc = manager.get_price("BTCUSDT")
        eth = manager.get_price("ETHUSDT")
        
        provider_name = manager.active_provider_name.value
        
        if btc:
            logger.info(f"[{i+1}/10] {provider_name} | BTC: ${btc.price}")
        if eth:
            logger.info(f"[{i+1}/10] {provider_name} | ETH: ${eth.price}")

    # Demonstrate failover by manually disconnecting primary
    logger.info("\n" + "=" * 70)
    logger.info("🔧 SIMULATING PRIMARY PROVIDER FAILURE")
    logger.info("=" * 70)
    
    logger.warning("\n⚠️  Manually disconnecting primary provider (Binance)...")
    primary_provider = manager._providers[Provider.BINANCE]
    await primary_provider.disconnect()
    
    logger.info("⏳ Waiting for failover to occur (health check will detect it)...")
    await asyncio.sleep(15)  # Wait for health check to detect and failover
    
    logger.info(f"\n✅ Current provider: {manager.active_provider_name.value}")
    logger.info(f"   Is using primary: {manager.is_using_primary}")

    # Monitor with fallback provider
    logger.info("\n⏱️  Monitoring with fallback provider for 10 seconds...")
    for i in range(10):
        await asyncio.sleep(1)
        btc = manager.get_price("BTCUSDT")
        eth = manager.get_price("ETHUSDT")
        
        provider_name = manager.active_provider_name.value
        
        if btc:
            logger.info(f"[{i+1}/10] {provider_name} | BTC: ${btc.price}")
        if eth:
            logger.info(f"[{i+1}/10] {provider_name} | ETH: ${eth.price}")

    # Wait for reconnection to primary
    logger.info("\n" + "=" * 70)
    logger.info("🔄 WAITING FOR PRIMARY PROVIDER RECOVERY")
    logger.info("=" * 70)
    logger.info("\n⏳ Reconnection loop should be attempting to restore primary...")
    logger.info("   (This may take up to 30 seconds)")
    
    await asyncio.sleep(30)
    
    logger.info(f"\n✅ Current provider: {manager.active_provider_name.value}")
    logger.info(f"   Is using primary: {manager.is_using_primary}")

    if manager.is_using_primary:
        logger.success("\n🎉 Successfully recovered to primary provider!")
    else:
        logger.warning("\n⚠️  Still using fallback/disaster provider")

    # Clean up
    logger.info("\n📊 Unsubscribing from symbols...")
    await manager.unsubscribe("BTCUSDT")
    await manager.unsubscribe("ETHUSDT")

    logger.info("\n🛑 Stopping manager...")
    await manager.stop()

    logger.success("\n✅ Failover test completed!")


if __name__ == "__main__":
    setup_logging()
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  Test interrupted by user")
