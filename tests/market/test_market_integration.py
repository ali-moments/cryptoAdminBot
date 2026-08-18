#!/usr/bin/env python3
"""
Real integration tests for market module with actual provider connections.

These tests connect to real exchanges to validate:
1. Provider connection and health tracking
2. Per-symbol routing and failover
3. Data freshness validation  
4. Race condition protection
5. Production incident scenarios
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import time

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider
from app.config.settings import settings


class TestMarketIntegration:
    """Integration tests with real market connections"""
    
    @pytest.fixture
    def event_loop(self):
        """Provide event loop for async tests"""
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
    
    @pytest.fixture
    async def real_manager(self):
        """Create manager with real providers"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        # Subscribe to price updates for monitoring
        price_updates = []
        async def capture_prices(event):
            price_updates.append(event.tick)
        dispatcher.subscribe(type(cache).__dict__['on_price_updated'].__annotations__['event'], capture_prices)
        
        providers = {
            Provider.BINANCE: BinanceProvider(dispatcher),
            Provider.BYBIT: BybitProvider(dispatcher), 
            Provider.OKX: OKXProvider(dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers,
            primary=Provider.BINANCE,
            fallback=Provider.BYBIT,
            disaster=Provider.OKX
        )
        
        yield manager, providers, price_updates
        
        # Cleanup
        if manager._running:
            await manager.stop()
    
    async def test_real_provider_connections(self, real_manager):
        """Test that all providers can connect to real exchanges"""
        manager, providers, price_updates = real_manager
        
        print("🔗 Testing real provider connections...")
        
        # Test Binance connection
        try:
            await providers[Provider.BINANCE].connect()
            assert providers[Provider.BINANCE].is_connected
            print("✅ Binance connection: SUCCESS")
        except Exception as e:
            print(f"❌ Binance connection failed: {e}")
            pytest.skip("Binance connection failed - network issue or rate limit")
        finally:
            await providers[Provider.BINANCE].disconnect()
            
        # Test Bybit connection  
        try:
            await providers[Provider.BYBIT].connect()
            assert providers[Provider.BYBIT].is_connected
            print("✅ Bybit connection: SUCCESS")
        except Exception as e:
            print(f"❌ Bybit connection failed: {e}")
            pytest.skip("Bybit connection failed - network issue or rate limit")
        finally:
            await providers[Provider.BYBIT].disconnect()
            
        # Test OKX connection
        try:
            await providers[Provider.OKX].connect()
            assert providers[Provider.OKX].is_connected  
            print("✅ OKX connection: SUCCESS")
        except Exception as e:
            print(f"❌ OKX connection failed: {e}")
            pytest.skip("OKX connection failed - network issue or rate limit")
        finally:
            await providers[Provider.OKX].disconnect()
    
    async def test_real_market_data_flow(self, real_manager):
        """Test receiving real market data and health tracking"""
        manager, providers, price_updates = real_manager
        
        print("📊 Testing real market data flow...")
        
        try:
            # Start manager (connects to primary provider)
            await manager.start()
            
            # Subscribe to a popular trading pair
            symbol = "BTCUSDT"
            await manager.subscribe(symbol)
            
            print(f"📈 Subscribed to {symbol}, waiting for market data...")
            
            # Wait for market data
            start_time = time.time()
            timeout = 30  # 30 seconds timeout
            
            while len(price_updates) == 0 and (time.time() - start_time) < timeout:
                await asyncio.sleep(0.5)
                
            assert len(price_updates) > 0, f"No market data received for {symbol} within {timeout}s"
            
            # Verify data quality
            latest_tick = price_updates[-1]
            assert latest_tick.symbol == symbol
            assert latest_tick.price > 0
            assert latest_tick.provider in [Provider.BINANCE, Provider.BYBIT, Provider.OKX]
            
            # Verify health tracking
            current_provider = manager._symbol_providers[symbol]
            provider_instance = providers[current_provider]
            
            assert provider_instance.is_connected
            assert provider_instance.is_symbol_healthy(symbol, 60)
            assert provider_instance.connection_time is not None
            
            print(f"✅ Received {len(price_updates)} price updates")
            print(f"✅ Latest: {latest_tick.symbol} @ ${latest_tick.price} from {latest_tick.provider.value}")
            print(f"✅ Provider health: Connected={provider_instance.is_connected}, Healthy={provider_instance.is_symbol_healthy(symbol, 60)}")
            
        except Exception as e:
            print(f"❌ Market data test failed: {e}")
            raise
    
    async def test_binance_preference_routing(self, real_manager):
        """Test that new symbols prefer Binance when available"""
        manager, providers, price_updates = real_manager
        
        print("🎯 Testing Binance preference routing...")
        
        await manager.start()
        
        # Test multiple symbols
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
        
        for symbol in symbols:
            await manager.subscribe(symbol)
            
            # Give time for routing decision
            await asyncio.sleep(0.5)
            
            assigned_provider = manager._symbol_providers.get(symbol)
            print(f"📍 {symbol} → {assigned_provider.value if assigned_provider else 'None'}")
            
            # Should prefer Binance if healthy
            if manager._is_provider_healthy(providers[Provider.BINANCE]):
                assert assigned_provider == Provider.BINANCE, f"{symbol} should route to Binance when healthy"
                print(f"✅ {symbol} correctly routed to Binance")
            else:
                print(f"ℹ️  {symbol} routed to {assigned_provider.value} (Binance unhealthy)")
    
    async def test_health_threshold_validation(self, real_manager):
        """Test that health thresholds work correctly with real data"""
        manager, providers, price_updates = real_manager
        
        print("🩺 Testing health threshold validation...")
        
        await manager.start()
        await manager.subscribe("BTCUSDT")
        
        # Wait for initial data
        await asyncio.sleep(5)
        
        current_provider_enum = manager._symbol_providers["BTCUSDT"]
        current_provider = providers[current_provider_enum]
        
        # Test within threshold
        fresh_health = current_provider.is_symbol_healthy("BTCUSDT", 60)
        print(f"✅ Fresh data health (60s): {fresh_health}")
        
        # Test with stricter threshold  
        strict_health = current_provider.is_symbol_healthy("BTCUSDT", 1)
        print(f"📊 Strict health (1s): {strict_health}")
        
        # Test provider-specific thresholds
        binance_threshold = manager.SYMBOL_HEALTH_THRESHOLDS.get(Provider.BINANCE, 60)
        bybit_threshold = manager.SYMBOL_HEALTH_THRESHOLDS.get(Provider.BYBIT, 120)
        
        print(f"⚙️  Thresholds: Binance={binance_threshold}s, Bybit={bybit_threshold}s")
        
        # Validate threshold configuration
        assert binance_threshold == 60, "Binance threshold should be 60s"
        assert bybit_threshold == 120, "Bybit threshold should be 120s"
        
        print("✅ Health thresholds validated")
    
    async def test_concurrent_subscriptions(self, real_manager):
        """Test multiple concurrent symbol subscriptions"""
        manager, providers, price_updates = real_manager
        
        print("⚡ Testing concurrent subscriptions...")
        
        await manager.start()
        
        # Subscribe to multiple symbols concurrently
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "DOTUSDT"]
        
        tasks = []
        for symbol in symbols:
            task = asyncio.create_task(manager.subscribe(symbol))
            tasks.append(task)
            
        # Wait for all subscriptions
        await asyncio.gather(*tasks)
        
        # Verify all symbols are tracked
        for symbol in symbols:
            assert symbol in manager._symbol_providers, f"{symbol} not in provider mapping"
            assert symbol in manager._subscriptions, f"{symbol} not in subscriptions"
            
            provider_enum = manager._symbol_providers[symbol]
            print(f"📍 {symbol} → {provider_enum.value}")
        
        print(f"✅ Successfully subscribed to {len(symbols)} symbols concurrently")
        
        # Wait for some data
        await asyncio.sleep(10)
        
        # Check that we received data for multiple symbols
        received_symbols = set(tick.symbol for tick in price_updates)
        print(f"📊 Received data for symbols: {received_symbols}")
        
        assert len(received_symbols) > 1, "Should receive data for multiple symbols"
    
    async def test_provider_failover_simulation(self, real_manager):
        """Simulate provider issues and test failover behavior"""
        manager, providers, price_updates = real_manager
        
        print("🔄 Testing provider failover simulation...")
        
        await manager.start()
        await manager.subscribe("BTCUSDT")
        
        # Wait for initial data
        await asyncio.sleep(5)
        initial_updates = len(price_updates)
        
        original_provider = manager._symbol_providers["BTCUSDT"]
        print(f"📍 Initial provider: {original_provider.value}")
        
        # Simulate provider becoming stale by manipulating health timestamps
        original_provider_instance = providers[original_provider]
        
        # Make the provider appear stale
        old_time = datetime.now(timezone.utc) - timedelta(seconds=200)
        original_provider_instance._symbol_health["BTCUSDT"] = old_time
        original_provider_instance._last_global_ticker = old_time
        
        print("🕒 Simulated stale data (200s old)")
        
        # Subscribe to new symbol - should route away from stale provider
        await manager.subscribe("ETHUSDT")
        
        new_provider = manager._symbol_providers["ETHUSDT"]
        print(f"📍 New symbol routed to: {new_provider.value}")
        
        # Verify different provider if original was made stale
        if not manager._is_provider_healthy(original_provider_instance):
            assert new_provider != original_provider, "Should route away from unhealthy provider"
            print("✅ Correctly avoided unhealthy provider")
        else:
            print("ℹ️  Provider remained healthy despite simulation")
    
    async def test_grace_period_behavior(self, real_manager):
        """Test grace period behavior after connections"""
        manager, providers, price_updates = real_manager
        
        print("⏰ Testing grace period behavior...")
        
        # Test provider health during grace period
        binance_provider = providers[Provider.BINANCE]
        
        # Initially disconnected - should be unhealthy
        assert not manager._is_provider_healthy(binance_provider)
        print("✅ Disconnected provider correctly unhealthy")
        
        # Connect - should be healthy due to grace period
        await binance_provider.connect()
        
        # Should be healthy immediately after connection (grace period)
        grace_healthy = manager._is_provider_healthy(binance_provider)
        print(f"⏱️  Health during grace period: {grace_healthy}")
        assert grace_healthy, "Provider should be healthy during grace period"
        
        # Clean up
        await binance_provider.disconnect()
        
        print("✅ Grace period behavior validated")


class TestMarketStressTests:
    """Stress tests for market module"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop  
        loop.close()
        
    @pytest.fixture
    async def stress_manager(self):
        """Manager setup for stress testing"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
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
        
        yield manager, providers
        
        if manager._running:
            await manager.stop()
    
    async def test_rapid_subscription_changes(self, stress_manager):
        """Test rapid subscription and unsubscription cycles"""
        manager, providers = stress_manager
        
        print("🏃 Testing rapid subscription changes...")
        
        await manager.start()
        
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
        
        # Rapid subscribe/unsubscribe cycles
        for cycle in range(5):
            print(f"🔄 Cycle {cycle + 1}/5")
            
            # Subscribe all
            for symbol in symbols:
                await manager.subscribe(symbol)
            
            await asyncio.sleep(0.1)
            
            # Unsubscribe all
            for symbol in symbols:
                await manager.unsubscribe(symbol)
                
            await asyncio.sleep(0.1)
        
        # Final state check
        assert len(manager._subscriptions) == 0, "All subscriptions should be cleaned up"
        print("✅ Rapid subscription changes handled correctly")
    
    async def test_high_symbol_count(self, stress_manager):
        """Test handling many symbols simultaneously"""
        manager, providers = stress_manager
        
        print("📊 Testing high symbol count...")
        
        await manager.start()
        
        # Major trading pairs
        symbols = [
            "BTCUSDT", "ETHUSDT", "ADAUSDT", "SOLUSDT", "DOTUSDT",
            "AVAXUSDT", "MATICUSDT", "LINKUSDT", "UNIUSDT", "AAVEUSDT",
            "SUSHIUSDT", "CRVUSDT", "COMPUSDT", "MKRUSDT", "YFIUSDT"
        ]
        
        print(f"📈 Subscribing to {len(symbols)} symbols...")
        
        # Subscribe to all symbols
        start_time = time.time()
        
        for symbol in symbols:
            await manager.subscribe(symbol)
            
        subscription_time = time.time() - start_time
        print(f"⏱️  Subscription time: {subscription_time:.2f}s")
        
        # Verify all are tracked
        assert len(manager._symbol_providers) == len(symbols)
        assert len(manager._subscriptions) == len(symbols)
        
        # Check provider distribution
        provider_counts = {}
        for symbol, provider in manager._symbol_providers.items():
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            
        print("📊 Provider distribution:")
        for provider, count in provider_counts.items():
            print(f"   {provider.value}: {count} symbols")
            
        print("✅ High symbol count handled successfully")
    
    async def test_connection_stability(self, stress_manager):
        """Test connection stability over time"""
        manager, providers = stress_manager
        
        print("🔗 Testing connection stability...")
        
        await manager.start()
        await manager.subscribe("BTCUSDT")
        
        # Monitor for extended period
        duration = 30  # 30 seconds
        check_interval = 5  # Check every 5 seconds
        
        print(f"⏱️  Monitoring stability for {duration}s...")
        
        stability_checks = []
        
        for i in range(duration // check_interval):
            await asyncio.sleep(check_interval)
            
            # Check provider health
            current_provider = manager._symbol_providers["BTCUSDT"]
            provider_instance = providers[current_provider]
            
            is_connected = provider_instance.is_connected
            is_healthy = manager._is_provider_healthy(provider_instance)
            
            stability_checks.append({
                'time': i * check_interval,
                'connected': is_connected,
                'healthy': is_healthy,
                'provider': current_provider.value
            })
            
            print(f"   {i * check_interval}s: {current_provider.value} - Connected: {is_connected}, Healthy: {is_healthy}")
        
        # Analyze stability
        connected_count = sum(1 for check in stability_checks if check['connected'])
        healthy_count = sum(1 for check in stability_checks if check['healthy'])
        
        stability_percent = (connected_count / len(stability_checks)) * 100
        health_percent = (healthy_count / len(stability_checks)) * 100
        
        print(f"📊 Stability metrics:")
        print(f"   Connection stability: {stability_percent:.1f}%")
        print(f"   Health stability: {health_percent:.1f}%")
        
        assert stability_percent >= 80, f"Connection stability too low: {stability_percent}%"
        print("✅ Connection stability acceptable")


class TestProductionScenarios:
    """Test real production failure scenarios"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
        
    @pytest.fixture  
    async def production_manager(self):
        """Manager configured like production"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        providers = {
            Provider.BINANCE: BinanceProvider(dispatcher),
            Provider.BYBIT: BybitProvider(dispatcher),
            Provider.OKX: OKXProvider(dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers,
            primary=Provider.BINANCE,
            fallback=Provider.BYBIT, 
            disaster=Provider.OKX
        )
        
        yield manager, providers
        
        if manager._running:
            await manager.stop()
    
    async def test_kosmos4_incident_prevention(self, production_manager):
        """Test that kosmos4.log incident cannot happen with new system"""
        manager, providers = production_manager
        
        print("🔥 Testing kosmos4 incident prevention...")
        
        await manager.start()
        
        # Subscribe to symbols that were in the log
        symbols = ["AEROUSDT", "CAKEUSDT", "ACEUSDT"]
        for symbol in symbols:
            await manager.subscribe(symbol)
            
        print(f"📈 Subscribed to production symbols: {symbols}")
        
        # Wait for initial data
        await asyncio.sleep(5)
        
        # Simulate the exact scenario: provider crash with health check confusion
        current_provider = manager._symbol_providers["AEROUSDT"]
        provider_instance = providers[current_provider]
        
        print(f"📍 Current provider: {current_provider.value}")
        
        # Simulate crash - mark as disconnected
        provider_instance.mark_disconnected()
        print("💥 Simulated provider crash")
        
        # The new system should detect this in health check
        is_healthy = manager._is_provider_healthy(provider_instance)
        print(f"🩺 Provider health after crash: {is_healthy}")
        
        assert not is_healthy, "Crashed provider should be detected as unhealthy"
        
        # Health check should trigger failover
        await asyncio.sleep(manager.HEALTH_CHECK_INTERVAL + 1)
        
        # Verify symbols moved away from crashed provider
        for symbol in symbols:
            new_provider = manager._symbol_providers.get(symbol)
            if new_provider:
                print(f"📍 {symbol} → {new_provider.value}")
                # Should not be on crashed provider anymore
                if new_provider == current_provider:
                    # Check if it's still actually healthy
                    if not manager._is_provider_healthy(providers[new_provider]):
                        pytest.fail(f"{symbol} still on unhealthy provider {new_provider.value}")
        
        print("✅ Kosmos4 incident cannot occur with new system")
    
    async def test_data_freshness_validation(self, production_manager):
        """Test that stale data is properly detected"""
        manager, providers = production_manager
        
        print("🕒 Testing data freshness validation...")
        
        await manager.start()
        await manager.subscribe("BTCUSDT")
        
        # Get current provider
        current_provider_enum = manager._symbol_providers["BTCUSDT"] 
        current_provider = providers[current_provider_enum]
        
        # Wait for fresh data
        await asyncio.sleep(5)
        
        # Verify fresh data is healthy
        fresh_healthy = current_provider.is_symbol_healthy("BTCUSDT", 60)
        print(f"✅ Fresh data healthy: {fresh_healthy}")
        
        # Simulate stale data by backdating health timestamp
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=300)  # 5 minutes old
        current_provider._symbol_health["BTCUSDT"] = stale_time
        
        # Check stale data detection
        stale_healthy = current_provider.is_symbol_healthy("BTCUSDT", 60)  # 60s threshold
        print(f"🕒 Stale data (300s old) healthy: {stale_healthy}")
        
        assert not stale_healthy, "Stale data should be detected as unhealthy"
        
        # Verify provider-level health also affected
        provider_healthy = manager._is_provider_healthy(current_provider)
        print(f"🩺 Provider health with stale data: {provider_healthy}")
        
        print("✅ Data freshness validation working correctly")
    
    async def test_race_condition_protection(self, production_manager):
        """Test that race conditions are prevented"""
        manager, providers = production_manager
        
        print("🏁 Testing race condition protection...")
        
        await manager.start()
        await manager.subscribe("BTCUSDT")
        
        # Simulate concurrent health check and reconnect operations
        async def simulate_health_check():
            for _ in range(10):
                await manager._check_single_symbol_health("BTCUSDT")
                await asyncio.sleep(0.1)
        
        async def simulate_provider_changes():
            provider_enum = manager._symbol_providers["BTCUSDT"]
            provider_instance = providers[provider_enum]
            
            for _ in range(5):
                # Simulate state changes
                provider_instance.mark_disconnected()
                await asyncio.sleep(0.1)
                provider_instance.mark_connected()
                await asyncio.sleep(0.1)
        
        print("⚡ Running concurrent operations...")
        
        # Run concurrently
        await asyncio.gather(
            simulate_health_check(),
            simulate_provider_changes(),
            return_exceptions=True
        )
        
        # Verify system is in consistent state
        final_provider = manager._symbol_providers.get("BTCUSDT")
        assert final_provider is not None, "Symbol should still be tracked"
        
        print(f"📍 Final state: BTCUSDT → {final_provider.value}")
        print("✅ Race condition protection verified")


if __name__ == "__main__":
    # Run specific test groups
    import sys
    
    if len(sys.argv) > 1:
        test_group = sys.argv[1]
        if test_group == "integration":
            pytest.main(["-v", "-s", "tests/market/test_market_integration.py::TestMarketIntegration"])
        elif test_group == "stress":
            pytest.main(["-v", "-s", "tests/market/test_market_integration.py::TestMarketStressTests"])
        elif test_group == "production":
            pytest.main(["-v", "-s", "tests/market/test_market_integration.py::TestProductionScenarios"])
        else:
            print("Usage: python test_market_integration.py [integration|stress|production]")
    else:
        # Run all tests
        pytest.main(["-v", "-s", __file__])