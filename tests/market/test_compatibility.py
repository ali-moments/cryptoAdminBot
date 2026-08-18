"""
Compatibility test to verify other modules can still work with ProviderManager changes.

Tests that all public interfaces remain unchanged and behave correctly.
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, Mock
import aiohttp

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.base import BaseProvider
from app.market.dto import PriceTick
from datetime import datetime, UTC
from decimal import Decimal


class CompatibilityMockProvider(BaseProvider):
    """Simple mock provider for compatibility testing."""
    
    def __init__(self, dispatcher, name: Provider):
        super().__init__(dispatcher)
        self._name = name
        self._subscriptions: dict[str, int] = {}
        self.subscribe_calls = []
        self.unsubscribe_calls = []
        self._should_be_healthy = True
    
    @property
    def name(self) -> Provider:
        return self._name
    
    async def connect(self) -> None:
        self.mark_connected()
    
    async def disconnect(self) -> None:
        self.mark_disconnected()
    
    async def subscribe(self, symbol: str) -> None:
        self.subscribe_calls.append(symbol)
        count = self._subscriptions.get(symbol, 0)
        self._subscriptions[symbol] = count + 1
        
        # Simulate receiving data immediately for healthy providers
        if self._should_be_healthy:
            await self._simulate_tick(symbol)
    
    async def unsubscribe(self, symbol: str) -> None:
        self.unsubscribe_calls.append(symbol)
        count = self._subscriptions.get(symbol)
        if count and count == 1:
            del self._subscriptions[symbol]
        elif count:
            self._subscriptions[symbol] = count - 1
    
    async def current_price(self, symbol: str) -> PriceTick:
        return PriceTick(
            provider=self._name,
            symbol=symbol,
            price=Decimal("50000.00"),
            timestamp=datetime.now(UTC)
        )
    
    async def _simulate_tick(self, symbol: str):
        """Simulate receiving a ticker for this symbol"""
        tick = PriceTick(
            provider=self._name,
            symbol=symbol,
            price=Decimal("50000.00"),
            timestamp=datetime.now(UTC)
        )
        await self._publish_price(tick)
    
    def set_health(self, healthy: bool):
        """Control provider health for testing"""
        self._should_be_healthy = healthy


@pytest.fixture
async def compatibility_manager():
    """Create a ProviderManager for compatibility testing."""
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    binance = CompatibilityMockProvider(dispatcher, Provider.BINANCE)
    bybit = CompatibilityMockProvider(dispatcher, Provider.BYBIT)
    okx = CompatibilityMockProvider(dispatcher, Provider.OKX)
    
    providers = {
        Provider.BINANCE: binance,
        Provider.BYBIT: bybit,
        Provider.OKX: okx,
    }
    
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
    )
    
    # Properly start the manager
    await manager.start()
    
    # Connect backup providers for tests
    await bybit.connect()
    await okx.connect()
    
    yield manager, binance, bybit, okx, cache
    
    # Cleanup
    if manager._running:
        await manager.stop()


@pytest.mark.asyncio
async def test_public_properties_unchanged(compatibility_manager):
    """Test that all public properties still work as expected."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # Properties used by main.py and bootstrap.py
    assert hasattr(manager, 'active_provider')
    assert hasattr(manager, 'active_provider_name')
    assert hasattr(manager, 'is_using_primary')
    
    # Check property values
    assert manager.active_provider_name == Provider.BINANCE
    assert manager.is_using_primary == True
    assert manager.active_provider == binance


@pytest.mark.asyncio
async def test_start_stop_methods_unchanged(compatibility_manager):
    """Test that start/stop methods work as expected by main.py."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # These methods should exist and work
    assert hasattr(manager, 'start')
    assert hasattr(manager, 'stop')
    
    # Should be callable without errors
    manager._running = False
    await manager.start()
    assert manager._running == True
    
    await manager.stop()
    assert manager._running == False


@pytest.mark.asyncio
async def test_sync_method_unchanged_interface(compatibility_manager):
    """Test that sync() method interface is unchanged for SubscriptionManager."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # SubscriptionManager calls this method
    required_symbols = {"BTCUSDT", "ETHUSDT"}
    
    # Should not raise an exception
    await manager.sync(required_symbols)
    
    # Should subscribe to symbols
    assert "BTCUSDT" in binance.subscribe_calls
    assert "ETHUSDT" in binance.subscribe_calls


@pytest.mark.asyncio
async def test_get_price_method_unchanged(compatibility_manager):
    """Test that get_price() works for TrackingManager via PriceCache."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # Simulate price being added to cache (like real providers do)
    tick = PriceTick(
        provider=Provider.BINANCE,
        symbol="BTCUSDT",
        price=Decimal("50000.00"),
        timestamp=datetime.now(UTC)
    )
    cache._prices["BTCUSDT"] = tick
    
    # get_price should return the cached price
    retrieved_tick = manager.get_price("BTCUSDT")
    assert retrieved_tick == tick
    assert retrieved_tick.symbol == "BTCUSDT"
    assert retrieved_tick.price == Decimal("50000.00")


@pytest.mark.asyncio
async def test_subscribe_unsubscribe_methods_unchanged(compatibility_manager):
    """Test that subscribe/unsubscribe methods work as expected."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # Public methods should work
    await manager.subscribe("BTCUSDT")
    assert "BTCUSDT" in manager._subscriptions
    
    await manager.unsubscribe("BTCUSDT")
    assert "BTCUSDT" not in manager._subscriptions


@pytest.mark.asyncio
async def test_reference_counting_still_works(compatibility_manager):
    """Test that reference counting behavior is preserved for multiple subscribers."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # Multiple subscribers
    await manager.subscribe("BTCUSDT")
    await manager.subscribe("BTCUSDT")
    
    # Should have ref count of 2
    assert manager._subscriptions["BTCUSDT"] == 2
    # But only one provider-level subscription
    assert binance._subscriptions["BTCUSDT"] == 1
    
    # Unsubscribe once
    await manager.unsubscribe("BTCUSDT")
    assert manager._subscriptions["BTCUSDT"] == 1
    # Provider still subscribed
    assert "BTCUSDT" in binance._subscriptions
    
    # Unsubscribe again
    await manager.unsubscribe("BTCUSDT")
    assert "BTCUSDT" not in manager._subscriptions
    # Provider now unsubscribed
    assert "BTCUSDT" not in binance._subscriptions


@pytest.mark.asyncio
async def test_constructor_interface_unchanged(compatibility_manager):
    """Test that ProviderManager constructor interface is unchanged."""
    # bootstrap.py creates ProviderManager with these parameters
    dispatcher = EventDispatcher()
    cache = PriceCache()
    providers = {Provider.BINANCE: Mock()}
    
    # Should be able to create with same parameters
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
    )
    
    # Should have all expected attributes
    assert manager._dispatcher == dispatcher
    assert manager._cache == cache
    assert manager._providers == providers
    assert manager._primary == Provider.BINANCE
    assert manager._fallback == Provider.BYBIT
    assert manager._disaster == Provider.OKX


@pytest.mark.asyncio
async def test_events_still_published(compatibility_manager):
    """Test that provider events are still published for TrackingManager."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # Mock event handler
    events_received = []
    
    async def event_handler(event):
        events_received.append(event)
    
    # Subscribe to events (like TrackingManager does)
    from app.market.events import ProviderChangedEvent
    manager._dispatcher.subscribe(ProviderChangedEvent, event_handler)
    
    # Subscribe a symbol first so there's something to transfer
    await manager.subscribe("BTCUSDT")
    
    # Simulate provider switch
    await manager._switch_provider(Provider.BYBIT)
    
    # Event should have been published
    assert len(events_received) == 1
    assert events_received[0].previous == Provider.BINANCE
    assert events_received[0].current == Provider.BYBIT


@pytest.mark.asyncio  
async def test_backwards_compatibility_scenario(compatibility_manager):
    """Test a complete scenario as if called by other modules."""
    manager, binance, bybit, okx, cache = compatibility_manager
    
    # Scenario: SubscriptionManager sync + TrackingManager get_price
    
    # 1. SubscriptionManager calls sync (like it does every 5 seconds)
    required_symbols = {"BTCUSDT", "ETHUSDT"}
    await manager.sync(required_symbols)
    
    # 2. Simulate provider sending price updates (like real providers do)
    from app.market.events import PriceUpdatedEvent
    btc_tick = PriceTick(
        provider=Provider.BINANCE,
        symbol="BTCUSDT", 
        price=Decimal("45000.00"),
        timestamp=datetime.now(UTC)
    )
    
    # Manually add to cache (simulating what the event handler does)
    cache._prices["BTCUSDT"] = btc_tick
    
    # 3. TrackingManager reads price from cache
    cached_price = manager.get_price("BTCUSDT")
    assert cached_price is not None
    assert cached_price.symbol == "BTCUSDT"
    
    # 4. Everything works as before, no breaking changes


if __name__ == "__main__":
    pytest.main([__file__, "-v"])