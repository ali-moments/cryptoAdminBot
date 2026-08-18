#!/usr/bin/env python3
"""
Regression tests for per-symbol routing and health-based provider failover.

Tests the exact incidents from the production logs to ensure they're fixed.
"""
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock

from app.database.enums import Provider
from app.market.manager import ProviderManager
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.providers.base import BaseProvider
from app.market.dto import PriceTick


class MockProvider(BaseProvider):
    """Mock provider for testing"""
    
    def __init__(self, name: Provider, dispatcher: EventDispatcher):
        super().__init__(dispatcher)
        self._name = name
        self._should_connect = True
        self._should_provide_data = True
        self._subscribe_failures = set()
        self.subscribed_symbols = set()
        
    @property
    def name(self) -> Provider:
        return self._name
        
    async def connect(self):
        if self._should_connect:
            self.mark_connected()
        else:
            raise ConnectionError(f"Mock connection failure for {self._name.value}")
            
    async def disconnect(self):
        self.mark_disconnected()
        self.subscribed_symbols.clear()
        
    async def subscribe(self, symbol: str):
        if symbol in self._subscribe_failures:
            raise RuntimeError(f"Mock subscription failure for {symbol}")
        self.subscribed_symbols.add(symbol)
        
        # Simulate immediate data if provider should provide data
        if self._should_provide_data:
            await self._simulate_ticker(symbol)
            
    async def unsubscribe(self, symbol: str):
        self.subscribed_symbols.discard(symbol)
        
    async def current_price(self, symbol: str):
        return None  # Not used in these tests
        
    async def _simulate_ticker(self, symbol: str):
        """Simulate receiving a ticker update"""
        tick = PriceTick(
            provider=self._name,
            symbol=symbol,
            price=100.0,
            timestamp=datetime.now(timezone.utc)
        )
        await self._publish_price(tick)
        
    def set_connection_behavior(self, should_connect: bool):
        """Control whether connection attempts succeed"""
        self._should_connect = should_connect
        
    def set_data_behavior(self, should_provide_data: bool):
        """Control whether provider provides data after connection"""
        self._should_provide_data = should_provide_data
        
    def set_subscription_failure(self, symbol: str, should_fail: bool = True):
        """Control subscription failures for specific symbols"""
        if should_fail:
            self._subscribe_failures.add(symbol)
        else:
            self._subscribe_failures.discard(symbol)
            
    def simulate_data_stop(self):
        """Simulate data stream stopping (connection stays up but no tickers)"""
        self._should_provide_data = False
        # Clear existing symbol health to simulate stale data
        now = datetime.now(timezone.utc) 
        # Set health timestamps to old values (beyond threshold)
        for symbol in list(self._symbol_health.keys()):
            self._symbol_health[symbol] = now - timedelta(seconds=200)  # Very stale
        self._last_global_ticker = now - timedelta(seconds=200)  # Very stale

    def simulate_symbol_stale(self, symbol: str):
        """Simulate data stopping for a specific symbol only"""
        now = datetime.now(timezone.utc)
        self._symbol_health[symbol] = now - timedelta(seconds=200)  # Make this symbol very stale
        # Don't affect global ticker or other symbols


class TestPerSymbolRouting:
    """Test per-symbol routing behavior"""
    
    @pytest.fixture
    def event_loop(self):
        loop = asyncio.new_event_loop()
        yield loop
        loop.close()
        
    @pytest.fixture
    async def manager_setup(self):
        """Set up manager with mock providers"""
        dispatcher = EventDispatcher()
        cache = PriceCache()
        
        providers = {
            Provider.BINANCE: MockProvider(Provider.BINANCE, dispatcher),
            Provider.BYBIT: MockProvider(Provider.BYBIT, dispatcher),
            Provider.OKX: MockProvider(Provider.OKX, dispatcher),
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
        
        # Cleanup
        if manager._running:
            await manager.stop()
    
    async def test_single_symbol_stale_failover(self, manager_setup):
        """
        Test: Single symbol goes stale on Binance while others stay healthy 
        → only that symbol moves to Bybit
        """
        manager, providers = manager_setup
        
        # Start manager
        await manager.start()
        
        # Connect backup providers for failover testing
        await providers[Provider.BYBIT].connect()
        await providers[Provider.OKX].connect()
        
        # Subscribe to multiple symbols
        symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
        for symbol in symbols:
            await manager.subscribe(symbol)
            
        # Verify all symbols start on Binance
        for symbol in symbols:
            assert manager._symbol_providers[symbol] == Provider.BINANCE
            
        # Simulate BTCUSDT data stopping on Binance (only BTCUSDT, not other symbols)
        providers[Provider.BINANCE].simulate_symbol_stale("BTCUSDT")
        
        # Wait for one health check cycle
        await asyncio.sleep(manager.HEALTH_CHECK_INTERVAL + 1)
        
        # Only BTCUSDT should move to Bybit, others stay on Binance
        assert manager._symbol_providers["BTCUSDT"] == Provider.BYBIT
        assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE  
        assert manager._symbol_providers["ADAUSDT"] == Provider.BINANCE
        
        # Verify subscriptions
        assert "BTCUSDT" in providers[Provider.BYBIT].subscribed_symbols
        assert "ETHUSDT" in providers[Provider.BINANCE].subscribed_symbols
        assert "ADAUSDT" in providers[Provider.BINANCE].subscribed_symbols
        
    async def test_symbol_recovery_to_primary(self, manager_setup):
        """
        Test: Symbol's Binance feed recovers with confirmed fresh data 
        → it moves back, others unaffected throughout
        """
        manager, providers = manager_setup
        
        # Start manager
        await manager.start()
        
        # Connect backup providers for failover testing
        await providers[Provider.BYBIT].connect()
        await providers[Provider.OKX].connect()
        
        # Subscribe to symbols
        symbols = ["BTCUSDT", "ETHUSDT"]
        for symbol in symbols:
            await manager.subscribe(symbol)
            
        # Simulate BTCUSDT moving to Bybit (due to Binance issues with that symbol)
        providers[Provider.BINANCE].simulate_symbol_stale("BTCUSDT")
        await asyncio.sleep(manager.HEALTH_CHECK_INTERVAL + 1)
        
        assert manager._symbol_providers["BTCUSDT"] == Provider.BYBIT
        assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE
        
        # Simulate Binance recovery with fresh data
        providers[Provider.BINANCE].set_data_behavior(True)
        await providers[Provider.BINANCE]._simulate_ticker("BTCUSDT")
        
        # Wait for grace period + health check
        await asyncio.sleep(manager.GRACE_PERIOD + manager.HEALTH_CHECK_INTERVAL + 1)
        
        # BTCUSDT should move back to Binance, ETHUSDT unaffected
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
        assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE
        
    async def test_kosmos4_incident_reproduction(self, manager_setup):
        """
        Test: Reproduce the exact kosmos4.log incident
        - BYBIT crashes but health check monitors wrong provider
        - Should be fixed with per-symbol routing
        """
        manager, providers = manager_setup
        
        # Start with Binance active (like log shows)
        await manager.start()
        
        # Connect backup providers for failover testing
        await providers[Provider.BYBIT].connect()
        await providers[Provider.OKX].connect()
        
        # Subscribe to symbols that were in the log
        symbols = ["AEROUSDT", "CAKEUSDT", "ACEUSDT"]
        for symbol in symbols:
            await manager.subscribe(symbol)
            
        # All symbols should start on Binance (primary)
        for symbol in symbols:
            assert manager._symbol_providers[symbol] == Provider.BINANCE
            
        # Simulate Binance crash (like 03:29:58 in log)
        providers[Provider.BINANCE].set_connection_behavior(False)
        providers[Provider.BINANCE].mark_disconnected()
        
        # Wait for health check to detect and failover
        await asyncio.sleep(manager.HEALTH_CHECK_INTERVAL + 1)
        
        # Symbols should move to Bybit
        for symbol in symbols:
            assert manager._symbol_providers[symbol] == Provider.BYBIT
            
        # Simulate Binance reconnection (like 03:30:16 in log) 
        providers[Provider.BINANCE].set_connection_behavior(True)
        providers[Provider.BINANCE].set_data_behavior(True)
        await providers[Provider.BINANCE].connect()
        
        # Wait for grace period + recovery detection
        await asyncio.sleep(manager.GRACE_PERIOD + manager.HEALTH_CHECK_INTERVAL + 1)
        
        # Symbols should move back to Binance with confirmed data
        for symbol in symbols:
            assert manager._symbol_providers[symbol] == Provider.BINANCE
            
        # Now simulate the exact BYBIT crash (like 03:32:49)
        providers[Provider.BYBIT].simulate_data_stop()
        providers[Provider.BYBIT].mark_disconnected()
        
        # The bug: health check should NOT be fooled by wrong provider
        # With our fix, symbols should stay on healthy Binance
        await asyncio.sleep(manager.HEALTH_CHECK_INTERVAL + 1)
        
        # Symbols should remain on Binance (no stale data)
        for symbol in symbols:
            assert manager._symbol_providers[symbol] == Provider.BINANCE
            
    async def test_no_race_conditions(self, manager_setup):
        """
        Test: Concurrent health-check and reconnect-loop activity 
        on the same symbol doesn't race
        """
        manager, providers = manager_setup
        
        await manager.start()
        
        # Connect backup providers for failover testing
        await providers[Provider.BYBIT].connect()
        await providers[Provider.OKX].connect()
        
        symbol = "BTCUSDT"
        await manager.subscribe(symbol)
        
        # Simulate rapid provider state changes
        async def rapid_state_changes():
            for _ in range(10):
                providers[Provider.BINANCE].simulate_data_stop()
                await asyncio.sleep(0.1)
                providers[Provider.BINANCE].set_data_behavior(True)
                await providers[Provider.BINANCE]._simulate_ticker(symbol)
                await asyncio.sleep(0.1)
                
        # Run state changes concurrently with health checks
        await asyncio.gather(
            rapid_state_changes(),
            asyncio.sleep(manager.HEALTH_CHECK_INTERVAL * 2)  # Let health check run
        )
        
        # Symbol should end up in a consistent state
        final_provider = manager._symbol_providers[symbol]
        assert final_provider in [Provider.BINANCE, Provider.BYBIT, Provider.OKX]
        
        # Provider assignment should be consistent with actual subscriptions
        provider_instance = providers[final_provider]
        assert symbol in provider_instance.subscribed_symbols
        
    async def test_binance_preference_maintained(self, manager_setup):
        """
        Test: New symbols always prefer Binance unless it's unhealthy
        """
        manager, providers = manager_setup
        
        await manager.start()
        
        # Connect backup providers for failover testing
        await providers[Provider.BYBIT].connect()
        await providers[Provider.OKX].connect()
        
        # Subscribe to symbol - should go to Binance
        await manager.subscribe("BTCUSDT")
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
        
        # Wait for grace period to expire, then make Binance stale
        await asyncio.sleep(manager.GRACE_PERIOD + 1)
        providers[Provider.BINANCE].simulate_data_stop()
        
        # Subscribe to new symbol - should go to Bybit (since Binance is stale)
        await manager.subscribe("ETHUSDT")  
        assert manager._symbol_providers["ETHUSDT"] == Provider.BYBIT
        
        # Restore Binance health with fresh data
        providers[Provider.BINANCE].set_data_behavior(True)
        await providers[Provider.BINANCE]._simulate_ticker("ADAUSDT")  # Fresh data
        
        # Subscribe to newer symbol - should prefer Binance again
        await manager.subscribe("ADAUSDT")
        assert manager._symbol_providers["ADAUSDT"] == Provider.BINANCE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])