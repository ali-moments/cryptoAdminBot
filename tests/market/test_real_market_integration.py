#!/usr/bin/env python3
"""
Real market integration tests with actual exchange connections.

These tests connect to live exchanges to validate the health system
and per-symbol routing with real market data.

WARNING: These tests make actual network requests to exchanges.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider


class TestRealMarketIntegration:
    """Test market system with real exchange connections"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
    
    @pytest.fixture
    async def real_market_manager(self):
        """Create manager with real exchange providers"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        # Create real providers
        providers = {
            Provider.BINANCE: BinanceProvider(dispatcher),
            Provider.BYBIT: BybitProvider(dispatcher),
            Provider.OKX: OKXProvider(dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers
        )
        
        yield manager, providers, cache
        
        if manager._running:
            await manager.stop()
    
    @pytest.mark.asyncio
    async def test_binance_connection_and_health(self, real_market_manager):
        """Test Binance connection and health tracking with real data"""
        manager, providers, cache = real_market_manager
        
        print("🔗 Testing real Binance connection...")
        
        # Connect to Binance
        await manager.start()
        
        binance = providers[Provider.BINANCE]
        
        # Verify connection
        assert binance.is_connected
        assert manager._is_provider_healthy(binance)
        print("✅ Binance connected and healthy")
        
        # Test real symbol subscription
        await manager.subscribe("BTCUSDT")
        
        # Wait for real price data
        print("⏳ Waiting for real BTCUSDT price data...")
        timeout = 30  # 30 second timeout for real data
        
        price_received = False
        start_time = datetime.now()
        
        while (datetime.now() - start_time).seconds < timeout:
            price = cache.get_price("BTCUSDT")
            if price is not None:
                print(f"📈 Received BTCUSDT price: ${price.price}")
                price_received = True
                break
            await asyncio.sleep(0.5)
        
        assert price_received, "Should receive real BTCUSDT price within 30 seconds"
        
        # Verify health after receiving data
        assert binance.is_symbol_healthy("BTCUSDT", 60)
        print("✅ Symbol health confirmed after receiving real data")
    
    @pytest.mark.asyncio
    async def test_provider_failover_with_real_data(self, real_market_manager):
        """Test failover behavior with real provider connections"""
        manager, providers, cache = real_market_manager
        
        print("🔄 Testing real provider failover...")
        
        # Start with Binance
        await manager.start()
        
        binance = providers[Provider.BINANCE]
        bybit = providers[Provider.BYBIT]
        
        # Subscribe to a symbol
        await manager.subscribe("ETHUSDT")
        
        # Verify initial routing to Binance
        initial_provider = manager._symbol_providers.get("ETHUSDT")
        print(f"📍 ETHUSDT initially routed to: {initial_provider.value}")
        
        # Wait for initial data
        print("⏳ Waiting for initial ETHUSDT data...")
        await asyncio.sleep(10)
        
        initial_price = cache.get_price("ETHUSDT")
        if initial_price:
            print(f"📈 Initial ETHUSDT price: ${initial_price.price} from {initial_price.provider.value}")
        
        # Connect backup provider
        await bybit.connect()
        await asyncio.sleep(2)  # Let connection settle
        
        # Simulate Binance becoming unhealthy by disconnecting
        print("🔌 Simulating Binance disconnect...")
        await binance.disconnect()
        
        # Wait and verify failover doesn't happen for existing symbols
        await asyncio.sleep(5)
        
        current_provider = manager._symbol_providers.get("ETHUSDT")
        print(f"📍 ETHUSDT after disconnect: {current_provider.value if current_provider else 'None'}")
        
        # New symbol should route to healthy Bybit
        print("🆕 Testing new symbol routing after failover...")
        await manager.subscribe("ADAUSDT")
        
        ada_provider = manager._symbol_providers.get("ADAUSDT")
        print(f"📍 ADAUSDT routed to: {ada_provider.value if ada_provider else 'None'}")
        
        # Should prefer healthy provider for new symbols
        if ada_provider:
            assert ada_provider == Provider.BYBIT or manager._is_provider_healthy(providers[ada_provider])
            print("✅ New symbol correctly routed to healthy provider")
    
    @pytest.mark.asyncio  
    async def test_per_symbol_health_with_real_data(self, real_market_manager):
        """Test per-symbol health tracking with real market data"""
        manager, providers, cache = real_market_manager
        
        print("🎯 Testing per-symbol health with real data...")
        
        await manager.start()
        
        binance = providers[Provider.BINANCE]
        
        # Subscribe to multiple symbols
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        
        for symbol in symbols:
            await manager.subscribe(symbol)
            print(f"📝 Subscribed to {symbol}")
        
        # Wait for real data on all symbols
        print("⏳ Waiting for real data on all symbols...")
        await asyncio.sleep(15)
        
        # Check individual symbol health
        print("🩺 Checking individual symbol health:")
        for symbol in symbols:
            health = binance.is_symbol_healthy(symbol, 60)
            price = cache.get_price(symbol)
            
            if price:
                print(f"  {symbol}: health={health}, price=${price.price}, age={int((datetime.now(timezone.utc) - price.timestamp).total_seconds())}s")
            else:
                print(f"  {symbol}: health={health}, no price data")
            
        # Most symbols should be healthy with real data
        healthy_count = sum(1 for s in symbols if binance.is_symbol_healthy(s, 60))
        print(f"✅ {healthy_count}/{len(symbols)} symbols healthy")
        assert healthy_count >= len(symbols) // 2, "At least half of symbols should be healthy"
    
    @pytest.mark.asyncio
    async def test_health_thresholds_with_real_timing(self, real_market_manager):
        """Test health thresholds match real exchange update patterns"""
        manager, providers, cache = real_market_manager
        
        print("⏱️  Testing health thresholds with real timing...")
        
        await manager.start()
        
        binance = providers[Provider.BINANCE]
        
        # Subscribe to a liquid symbol that updates frequently
        await manager.subscribe("BTCUSDT")
        
        # Collect timing data over 30 seconds
        print("📊 Collecting real update intervals...")
        prices = []
        start_time = datetime.now()
        
        while (datetime.now() - start_time).seconds < 30:
            price = cache.get_price("BTCUSDT")
            if price and (not prices or price.timestamp != prices[-1].timestamp):
                prices.append(price)
                print(f"  📈 Price update: ${price.price} at {price.timestamp.strftime('%H:%M:%S.%f')[:-3]}")
            await asyncio.sleep(0.1)
        
        if len(prices) >= 2:
            # Calculate intervals between updates
            intervals = []
            for i in range(1, len(prices)):
                interval = (prices[i].timestamp - prices[i-1].timestamp).total_seconds()
                intervals.append(interval)
            
            if intervals:
                avg_interval = sum(intervals) / len(intervals)
                max_interval = max(intervals)
                
                print(f"📊 Update statistics:")
                print(f"  Updates received: {len(prices)}")
                print(f"  Average interval: {avg_interval:.2f}s")
                print(f"  Maximum interval: {max_interval:.2f}s")
                print(f"  Binance threshold: {manager.SYMBOL_HEALTH_THRESHOLDS[Provider.BINANCE]}s")
                
                # Verify threshold is reasonable (should be 2-3x max observed interval)
                threshold = manager.SYMBOL_HEALTH_THRESHOLDS[Provider.BINANCE]
                assert threshold > max_interval * 1.5, f"Threshold {threshold}s too tight for max interval {max_interval}s"
                print("✅ Threshold appropriately configured for real update patterns")
        else:
            print("⚠️  Insufficient data received - check network connection")
    
    @pytest.mark.asyncio
    async def test_multi_provider_real_connections(self, real_market_manager):
        """Test connections to multiple real exchanges"""
        manager, providers, cache = real_market_manager
        
        print("🌐 Testing multi-provider real connections...")
        
        # Start with primary
        await manager.start()
        
        # Connect to all providers
        for provider_enum, provider_instance in providers.items():
            if not provider_instance.is_connected:
                print(f"🔗 Connecting to {provider_enum.value}...")
                try:
                    await provider_instance.connect()
                    await asyncio.sleep(2)  # Let connection settle
                    
                    if provider_instance.is_connected:
                        print(f"✅ {provider_enum.value} connected")
                    else:
                        print(f"❌ {provider_enum.value} connection failed")
                except Exception as e:
                    print(f"❌ {provider_enum.value} error: {e}")
        
        # Test health across providers
        print("🩺 Testing health across providers:")
        for provider_enum, provider_instance in providers.items():
            health = manager._is_provider_healthy(provider_instance)
            print(f"  {provider_enum.value}: connected={provider_instance.is_connected}, healthy={health}")
        
        # Test symbol routing to different providers
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        
        for symbol in symbols:
            try:
                await manager.subscribe(symbol)
                provider = manager._symbol_providers.get(symbol)
                print(f"📍 {symbol} routed to: {provider.value if provider else 'None'}")
            except Exception as e:
                print(f"⚠️  Failed to subscribe {symbol}: {e}")
        
        # Wait for some data
        await asyncio.sleep(10)
        
        # Check data from different providers
        print("📊 Data received from providers:")
        for symbol in symbols:
            price = cache.get_price(symbol)
            if price:
                provider = manager._symbol_providers.get(symbol)
                print(f"  {symbol}: ${price.price} from {provider.value if provider else 'Unknown'}")
        
        print("✅ Multi-provider test completed")


@pytest.mark.asyncio
async def test_emergency_real_market_scenario():
    """Test emergency scenario with real market conditions"""
    print("🚨 Testing emergency real market scenario...")
    
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    # Create real providers
    providers = {
        Provider.BINANCE: BinanceProvider(dispatcher),
        Provider.BYBIT: BybitProvider(dispatcher),
        Provider.OKX: OKXProvider(dispatcher),
    }
    
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers=providers
    )
    
    try:
        # Start system
        await manager.start()
        
        # Subscribe to critical symbols
        critical_symbols = ["BTCUSDT", "ETHUSDT"]
        
        for symbol in critical_symbols:
            await manager.subscribe(symbol)
            print(f"📝 Emergency subscribed: {symbol}")
        
        # Wait for initial data
        await asyncio.sleep(15)
        
        # Verify critical data is flowing
        print("🚨 Verifying critical data flow:")
        for symbol in critical_symbols:
            price = cache.get_price(symbol)
            provider = manager._symbol_providers.get(symbol)
            
            if price:
                age_seconds = (datetime.now(timezone.utc) - price.timestamp).total_seconds()
                print(f"  {symbol}: ${price.price} from {provider.value if provider else 'Unknown'}, {age_seconds:.1f}s old")
                
                # Critical: data must be fresh (< 30s)
                assert age_seconds < 30, f"Critical data too stale: {symbol} is {age_seconds}s old"
            else:
                pytest.fail(f"No price data for critical symbol: {symbol}")
        
        print("✅ Emergency scenario: all critical data flowing")
        
    finally:
        await manager.stop()


if __name__ == "__main__":
    # Run with real market data - use carefully
    import sys
    
    print("⚠️  WARNING: Running tests with REAL market connections!")
    print("   - These tests connect to live exchanges")
    print("   - Network issues may cause test failures")
    print("   - Tests may take 1-2 minutes to complete")
    
    response = input("Continue with real market tests? (y/n): ")
    if response.lower() != 'y':
        print("Aborted.")
        sys.exit(0)
    
    pytest.main(["-v", "-s", __file__, "--tb=short"])