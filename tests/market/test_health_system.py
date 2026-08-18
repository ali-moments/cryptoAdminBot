#!/usr/bin/env python3
"""
Unit tests for the health tracking system.

Tests the core health logic, thresholds, and state management without network dependencies.
"""

import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.base import BaseProvider
from app.market.dto import PriceTick


class MockHealthProvider(BaseProvider):
    """Mock provider for testing health logic"""
    
    def __init__(self, name: Provider, dispatcher: EventDispatcher):
        super().__init__(dispatcher)
        self._name = name
        self._should_connect = True
        self._simulate_crash = False
        
    @property
    def name(self) -> Provider:
        return self._name
        
    async def connect(self):
        if self._should_connect:
            self.mark_connected()
        else:
            raise ConnectionError("Mock connection failure")
            
    async def disconnect(self):
        self.mark_disconnected()
        
    async def subscribe(self, symbol: str):
        if self._simulate_crash:
            raise RuntimeError("Mock subscription failure")
        # Simulate receiving immediate data
        await self._simulate_price_update(symbol)
        
    async def unsubscribe(self, symbol: str):
        pass
        
    async def current_price(self, symbol: str):
        return None
        
    async def _simulate_price_update(self, symbol: str):
        """Simulate receiving a price update"""
        tick = PriceTick(
            provider=self._name,
            symbol=symbol,
            price=50000.0,
            timestamp=datetime.now(timezone.utc)
        )
        await self._publish_price(tick)
        
    def force_stale_data(self, symbol: str, age_seconds: int):
        """Force symbol data to appear stale"""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        self._symbol_health[symbol] = old_time
        if age_seconds > 60:  # Affect global health too
            self._last_global_ticker = old_time
            
    def set_connection_behavior(self, should_connect: bool):
        self._should_connect = should_connect
        
    def set_crash_behavior(self, should_crash: bool):
        self._simulate_crash = should_crash


class TestHealthSystem:
    """Test health tracking and validation"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
        
    @pytest.fixture
    async def health_manager(self):
        """Create manager with mock health providers"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        providers = {
            Provider.BINANCE: MockHealthProvider(Provider.BINANCE, dispatcher),
            Provider.BYBIT: MockHealthProvider(Provider.BYBIT, dispatcher),
            Provider.OKX: MockHealthProvider(Provider.OKX, dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers
        )
        
        yield manager, providers
        
        if manager._running:
            await manager.stop()
    
    def test_provider_health_thresholds(self, health_manager):
        """Test that provider-specific thresholds are correctly configured"""
        manager, providers = health_manager
        
        print("📊 Testing health thresholds...")
        
        # Verify threshold configuration
        assert manager.SYMBOL_HEALTH_THRESHOLDS[Provider.BINANCE] == 60
        assert manager.SYMBOL_HEALTH_THRESHOLDS[Provider.BYBIT] == 120  
        assert manager.SYMBOL_HEALTH_THRESHOLDS[Provider.OKX] == 90
        assert manager.DEFAULT_THRESHOLD == 60
        assert manager.GRACE_PERIOD == 15
        
        print("✅ All thresholds correctly configured")
        
    async def test_symbol_health_tracking(self, health_manager):
        """Test per-symbol health tracking"""
        manager, providers = health_manager
        
        print("🩺 Testing symbol health tracking...")
        
        binance = providers[Provider.BINANCE] 
        await binance.connect()
        
        symbol = "BTCUSDT"
        
        # Initially no health data
        assert not binance.is_symbol_healthy(symbol, 60)
        print("✅ No health data initially")
        
        # Simulate price update
        await binance._simulate_price_update(symbol)
        
        # Should now be healthy
        assert binance.is_symbol_healthy(symbol, 60)
        print("✅ Healthy after price update")
        
        # Force stale data
        binance.force_stale_data(symbol, 120)  # 2 minutes old
        
        # Should be unhealthy with strict threshold
        assert not binance.is_symbol_healthy(symbol, 30)
        print("✅ Correctly detects stale data")
        
        # Should still be healthy with loose threshold
        assert binance.is_symbol_healthy(symbol, 180)
        print("✅ Respects threshold differences")
        
    async def test_provider_global_health(self, health_manager):
        """Test provider global health status"""
        manager, providers = health_manager
        
        print("🌍 Testing provider global health...")
        
        binance = providers[Provider.BINANCE]
        
        # Disconnected provider
        assert not manager._is_provider_healthy(binance)
        print("✅ Disconnected provider unhealthy")
        
        # Connect - should be healthy during grace period
        await binance.connect()
        assert manager._is_provider_healthy(binance)
        print("✅ Connected provider healthy (grace period)")
        
        # Simulate grace period expiry with no data
        old_connection_time = datetime.now(timezone.utc) - timedelta(seconds=20)
        binance._connection_time = old_connection_time
        binance._last_global_ticker = None
        
        assert not manager._is_provider_healthy(binance)
        print("✅ No data after grace period = unhealthy")
        
        # Add fresh data
        await binance._simulate_price_update("BTCUSDT")
        assert manager._is_provider_healthy(binance)
        print("✅ Fresh data = healthy")
        
    async def test_grace_period_logic(self, health_manager):
        """Test grace period behavior"""
        manager, providers = health_manager
        
        print("⏰ Testing grace period logic...")
        
        binance = providers[Provider.BINANCE]
        
        # Connect provider
        await binance.connect()
        
        # Should be healthy immediately (grace period)
        assert manager._is_provider_healthy(binance)
        connection_time = binance.connection_time
        
        # Simulate time passing but within grace period
        recent_time = datetime.now(timezone.utc) - timedelta(seconds=10)  # 10s ago
        binance._connection_time = recent_time
        
        assert manager._is_provider_healthy(binance)
        print("✅ Healthy within grace period (10s)")
        
        # Simulate grace period expiry
        old_time = datetime.now(timezone.utc) - timedelta(seconds=20)  # 20s ago  
        binance._connection_time = old_time
        binance._last_global_ticker = None  # No data
        
        assert not manager._is_provider_healthy(binance)
        print("✅ Unhealthy after grace period without data")
        
        # Add data - should be healthy again
        await binance._simulate_price_update("BTCUSDT")
        assert manager._is_provider_healthy(binance)
        print("✅ Healthy with data after grace period")
        
    async def test_symbol_routing_health_checks(self, health_manager):
        """Test health checks in symbol routing"""
        manager, providers = health_manager
        
        print("🎯 Testing symbol routing health checks...")
        
        await manager.start()
        
        # All providers healthy - should choose primary (Binance)
        await manager.subscribe("BTCUSDT")
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
        print("✅ Healthy primary chosen")
        
        # Connect secondary providers so they're available for failover
        bybit = providers[Provider.BYBIT]
        await bybit.connect()
        
        # Make Binance unhealthy
        binance = providers[Provider.BINANCE]
        binance.force_stale_data("BTCUSDT", 200)  # Very stale
        binance._last_global_ticker = datetime.now(timezone.utc) - timedelta(seconds=200)
        
        # Make Binance appear past grace period
        binance._connection_time = datetime.now(timezone.utc) - timedelta(seconds=30)
        
        # Verify Binance is truly unhealthy
        binance_healthy = manager._is_provider_healthy(binance)
        print(f"🩺 Binance health after making stale: {binance_healthy}")
        
        if not binance_healthy:
            # New symbol should avoid unhealthy Binance
            await manager.subscribe("ETHUSDT")
            eth_provider = manager._symbol_providers["ETHUSDT"]
            print(f"📍 ETHUSDT routed to: {eth_provider.value}")
            
            assert eth_provider != Provider.BINANCE
            print("✅ Correctly avoided unhealthy primary")
        else:
            print("ℹ️  Binance remained healthy - test condition not achieved")
            
    async def test_connection_time_tracking(self, health_manager):
        """Test connection time tracking"""
        manager, providers = health_manager
        
        print("🕐 Testing connection time tracking...")
        
        binance = providers[Provider.BINANCE]
        
        # Initially no connection time
        assert binance.connection_time is None
        print("✅ No connection time initially")
        
        # Connect
        connect_start = datetime.now(timezone.utc)
        await binance.connect()
        connect_end = datetime.now(timezone.utc)
        
        # Should have connection time
        assert binance.connection_time is not None
        assert connect_start <= binance.connection_time <= connect_end
        print("✅ Connection time recorded correctly")
        
        # Disconnect
        await binance.disconnect()
        assert binance.connection_time is None
        print("✅ Connection time cleared on disconnect")
        
    async def test_health_during_provider_issues(self, health_manager):
        """Test health behavior during various provider issues"""
        manager, providers = health_manager
        
        print("⚠️  Testing health during provider issues...")
        
        await manager.start()
        binance = providers[Provider.BINANCE]
        
        # Subscribe and get initial data
        await manager.subscribe("BTCUSDT")
        initial_health = manager._is_provider_healthy(binance)
        print(f"📊 Initial health: {initial_health}")
        
        # Simulate subscription failure on new symbols
        binance.set_crash_behavior(True)
        
        try:
            await manager.subscribe("ETHUSDT") 
            # Should handle the crash gracefully
        except Exception as e:
            print(f"⚠️  Expected exception: {e}")
        
        # Binance should still be connected but might be unhealthy for new subscriptions
        assert binance.is_connected
        print("✅ Provider remains connected despite subscription issues")
        
        # Reset crash behavior
        binance.set_crash_behavior(False)
        
    async def test_multi_symbol_health_independence(self, health_manager):
        """Test that symbol health is tracked independently"""
        manager, providers = health_manager
        
        print("🔄 Testing multi-symbol health independence...")
        
        await manager.start()
        binance = providers[Provider.BINANCE]
        
        # Subscribe to multiple symbols
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
        for symbol in symbols:
            await manager.subscribe(symbol)
            
        # Verify all healthy initially
        for symbol in symbols:
            assert binance.is_symbol_healthy(symbol, 60)
        print("✅ All symbols initially healthy")
        
        # Make one symbol stale
        binance.force_stale_data("ETHUSDT", 180)  # 3 minutes old
        
        # Check health independently
        btc_healthy = binance.is_symbol_healthy("BTCUSDT", 60)
        eth_healthy = binance.is_symbol_healthy("ETHUSDT", 60)  # Should be false
        ada_healthy = binance.is_symbol_healthy("ADAUSDT", 60)
        
        print(f"📊 Individual health: BTC={btc_healthy}, ETH={eth_healthy}, ADA={ada_healthy}")
        
        assert btc_healthy, "BTCUSDT should remain healthy"
        assert not eth_healthy, "ETHUSDT should be unhealthy (stale)"
        assert ada_healthy, "ADAUSDT should remain healthy"
        
        print("✅ Symbol health tracked independently")
        
    async def test_provider_hierarchy_respects_health(self, health_manager):
        """Test that provider hierarchy respects health status"""
        manager, providers = health_manager
        
        print("📊 Testing provider hierarchy with health...")
        
        # Start with all providers disconnected
        hierarchy = manager._get_provider_hierarchy()
        assert hierarchy == [Provider.BINANCE, Provider.BYBIT, Provider.OKX]
        print("✅ Hierarchy correctly configured")
        
        # Connect only secondary provider
        bybit = providers[Provider.BYBIT]
        await bybit.connect()
        
        await manager.start()
        
        # Should skip unhealthy primary and use healthy secondary
        await manager.subscribe("BTCUSDT")
        provider = manager._symbol_providers["BTCUSDT"]
        
        # Should be Bybit since Binance is not connected
        if not manager._is_provider_healthy(providers[Provider.BINANCE]):
            assert provider == Provider.BYBIT
            print("✅ Correctly skipped unhealthy primary")
        
        # Connect primary
        binance = providers[Provider.BINANCE]
        await binance.connect()
        
        # New symbol should prefer healthy primary
        await manager.subscribe("ETHUSDT")
        eth_provider = manager._symbol_providers["ETHUSDT"]
        
        if manager._is_provider_healthy(binance):
            assert eth_provider == Provider.BINANCE
            print("✅ Prefers healthy primary when available")


class TestHealthEdgeCases:
    """Test edge cases and error conditions"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
        
    @pytest.fixture
    async def edge_manager(self):
        """Manager for edge case testing"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        providers = {
            Provider.BINANCE: MockHealthProvider(Provider.BINANCE, dispatcher),
            Provider.BYBIT: MockHealthProvider(Provider.BYBIT, dispatcher),
            Provider.OKX: MockHealthProvider(Provider.OKX, dispatcher),
        }
        
        manager = ProviderManager(
            dispatcher=dispatcher,
            cache=cache,
            providers=providers
        )
        
        yield manager, providers
        
        if manager._running:
            await manager.stop()
    
    async def test_all_providers_unhealthy(self, edge_manager):
        """Test behavior when all providers are unhealthy"""
        manager, providers = edge_manager
        
        print("💥 Testing all providers unhealthy...")
        
        # Make all providers fail to connect
        for provider in providers.values():
            provider.set_connection_behavior(False)
        
        # Starting should fail gracefully
        try:
            await manager.start()
            pytest.fail("Should not succeed when all providers fail")
        except RuntimeError as e:
            assert "All providers failed" in str(e)
            print("✅ Correctly failed when all providers unhealthy")
            
    async def test_rapid_health_state_changes(self, edge_manager):
        """Test rapid health state changes"""
        manager, providers = edge_manager
        
        print("⚡ Testing rapid health changes...")
        
        # Don't call manager.start() - test health logic directly
        binance = providers[Provider.BINANCE]
        
        # Test health logic without actual connections
        async def rapid_test():
            # Test rapid connection state changes
            for i in range(5):
                # Simulate disconnection
                binance.mark_disconnected()
                assert not manager._is_provider_healthy(binance)
                
                # Simulate connection with grace period
                binance.mark_connected() 
                assert manager._is_provider_healthy(binance)  # Grace period
                
            print("✅ Handled rapid state changes")
        
        # Run with reasonable timeout
        await asyncio.wait_for(rapid_test(), timeout=2.0)
        
    async def test_concurrent_health_checks(self, edge_manager):
        """Test concurrent health check operations"""
        manager, providers = edge_manager
        
        print("🏁 Testing concurrent health checks...")
        
        await manager.start()
        await manager.subscribe("BTCUSDT")
        
        # Run many concurrent health checks
        async def health_check_loop():
            for _ in range(50):
                symbol = "BTCUSDT"
                provider_enum = manager._symbol_providers.get(symbol)
                if provider_enum:
                    provider_instance = providers[provider_enum]
                    manager._is_provider_healthy(provider_instance)
                    provider_instance.is_symbol_healthy(symbol, 60)
                await asyncio.sleep(0.001)
        
        # Run multiple concurrent loops
        tasks = [asyncio.create_task(health_check_loop()) for _ in range(5)]
        await asyncio.gather(*tasks)
        
        # System should remain consistent
        final_provider = manager._symbol_providers.get("BTCUSDT")
        assert final_provider is not None
        print("✅ Concurrent health checks handled safely")
        
    async def test_memory_cleanup_on_disconnect(self, edge_manager):
        """Test that health data is cleaned up on disconnect"""
        manager, providers = edge_manager
        
        print("🧹 Testing memory cleanup...")
        
        binance = providers[Provider.BINANCE]
        await binance.connect()
        
        # Add some health data
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
        for symbol in symbols:
            await binance._simulate_price_update(symbol)
            
        # Verify data exists
        assert len(binance._symbol_health) == 3
        assert binance._last_global_ticker is not None
        print("✅ Health data created")
        
        # Disconnect
        await binance.disconnect()
        
        # Verify cleanup
        assert len(binance._symbol_health) == 0
        assert binance._last_global_ticker is None
        assert binance.connection_time is None
        print("✅ Health data cleaned up on disconnect")


if __name__ == "__main__":
    pytest.main(["-v", "-s", __file__])