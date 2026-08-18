"""
Tests for per-symbol provider selection and failover.

Tests validate that:
1. Symbols can be distributed across multiple providers
2. Symbol-level provider selection works correctly
3. Provider-level failures only affect symbols on that provider
4. Reference counting is preserved across providers
5. Sync preserves reference counts
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, Mock, patch
import aiohttp

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.base import BaseProvider
from app.market.dto import PriceTick
from datetime import datetime, UTC
from decimal import Decimal


# ===========================================================================
# Mock Provider
# ===========================================================================

class MockProvider(BaseProvider):
    """Mock provider for testing."""
    
    def __init__(self, dispatcher, name: Provider, supported_symbols: set[str] | None = None):
        super().__init__(dispatcher)
        self._name = name
        self._supported_symbols = supported_symbols or set()
        self._subscriptions: dict[str, int] = {}
        self._subscribe_calls: list[str] = []
        self._unsubscribe_calls: list[str] = []
    
    @property
    def name(self) -> Provider:
        return self._name
    
    async def connect(self) -> None:
        self.mark_connected()
    
    async def disconnect(self) -> None:
        self.mark_disconnected()
    
    async def subscribe(self, symbol: str) -> None:
        # Check if symbol is supported first
        if symbol not in self._supported_symbols:
            raise RuntimeError(f"Symbol {symbol} not supported")
            
        self._subscribe_calls.append(symbol)
        count = self._subscriptions.get(symbol, 0)
        self._subscriptions[symbol] = count + 1
        
        # Simulate receiving data for supported symbols
        await self._simulate_tick(symbol)
    
    async def unsubscribe(self, symbol: str) -> None:
        self._unsubscribe_calls.append(symbol)
        count = self._subscriptions.get(symbol)
        if count and count == 1:
            del self._subscriptions[symbol]
        elif count:
            self._subscriptions[symbol] = count - 1
    
    async def current_price(self, symbol: str) -> PriceTick:
        """Check if symbol is supported."""
        if symbol not in self._supported_symbols:
            # Simulate HTTP 400 error for unsupported symbols
            error = aiohttp.ClientResponseError(
                request_info=Mock(),
                history=(),
                status=400,
                message=f"Symbol {symbol} not found"
            )
            raise error
        
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


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
async def manager_with_mocks():
    """Create a ProviderManager with mock providers."""
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    # Create mock providers with different symbol support
    binance = MockProvider(dispatcher, Provider.BINANCE, {"BTCUSDT", "ETHUSDT"})
    bybit = MockProvider(dispatcher, Provider.BYBIT, {"BTCUSDT", "ETHUSDT", "XYZUSDT"})
    okx = MockProvider(dispatcher, Provider.OKX, {"BTCUSDT"})
    
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
    
    # Start the manager properly
    await manager.start()
    
    # Connect backup providers
    await bybit.connect()
    await okx.connect()
    
    yield manager, binance, bybit, okx
    
    # Cleanup
    if manager._running:
        await manager.stop()


# ===========================================================================
# Tests
# ===========================================================================

@pytest.mark.asyncio
async def test_subscribe_symbol_to_primary(manager_with_mocks):
    """Test subscribing a symbol that's supported by primary provider."""
    manager, binance, bybit, okx = manager_with_mocks
    
    await manager.subscribe("BTCUSDT")
    
    # Should be on Binance (primary)
    assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
    assert manager._subscriptions["BTCUSDT"] == 1
    assert "BTCUSDT" in binance._subscribe_calls
    assert "BTCUSDT" not in bybit._subscribe_calls


@pytest.mark.asyncio
async def test_subscribe_symbol_fallback_when_primary_unavailable(manager_with_mocks):
    """Test subscribing a symbol unsupported by primary falls back to Bybit."""
    manager, binance, bybit, okx = manager_with_mocks
    
    await manager.subscribe("XYZUSDT")
    
    # Should be on Bybit (fallback) because Binance doesn't support it
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT
    assert manager._subscriptions["XYZUSDT"] == 1
    assert "XYZUSDT" not in binance._subscribe_calls
    assert "XYZUSDT" in bybit._subscribe_calls


@pytest.mark.asyncio
async def test_reference_counting_across_providers(manager_with_mocks):
    """Test reference counting works correctly with per-symbol providers."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe BTCUSDT twice (should go to Binance)
    await manager.subscribe("BTCUSDT")
    await manager.subscribe("BTCUSDT")
    
    assert manager._subscriptions["BTCUSDT"] == 2
    assert len(binance._subscribe_calls) == 1  # Only subscribed once at provider level
    
    # Unsubscribe once
    await manager.unsubscribe("BTCUSDT")
    assert manager._subscriptions["BTCUSDT"] == 1
    assert len(binance._unsubscribe_calls) == 0  # Not unsubscribed yet
    
    # Unsubscribe again
    await manager.unsubscribe("BTCUSDT")
    assert "BTCUSDT" not in manager._subscriptions
    assert "BTCUSDT" not in manager._symbol_providers
    assert len(binance._unsubscribe_calls) == 1  # Now unsubscribed


@pytest.mark.asyncio
async def test_per_symbol_unsubscribe(manager_with_mocks):
    """Test unsubscribing from the correct provider."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe symbols to different providers
    await manager.subscribe("BTCUSDT")  # Goes to Binance
    await manager.subscribe("XYZUSDT")  # Goes to Bybit
    
    # Unsubscribe BTCUSDT
    await manager.unsubscribe("BTCUSDT")
    
    # Should only unsubscribe from Binance
    assert "BTCUSDT" in binance._unsubscribe_calls
    assert "BTCUSDT" not in bybit._unsubscribe_calls
    
    # XYZUSDT should still be subscribed on Bybit
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT


@pytest.mark.asyncio
async def test_mixed_provider_subscriptions(manager_with_mocks):
    """Test symbols can be distributed across multiple providers."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe multiple symbols
    await manager.subscribe("BTCUSDT")  # Binance
    await manager.subscribe("ETHUSDT")  # Binance
    await manager.subscribe("XYZUSDT")  # Bybit (not on Binance)
    
    # Check distribution
    assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
    assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT
    
    # Check provider subscriptions
    assert "BTCUSDT" in binance._subscribe_calls
    assert "ETHUSDT" in binance._subscribe_calls
    assert "XYZUSDT" not in binance._subscribe_calls
    assert "XYZUSDT" in bybit._subscribe_calls


@pytest.mark.asyncio
async def test_provider_switch_only_moves_affected_symbols(manager_with_mocks):
    """Test provider failover only affects symbols on failed provider."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe symbols to different providers
    await manager.subscribe("BTCUSDT")  # Binance
    await manager.subscribe("ETHUSDT")  # Binance
    await manager.subscribe("XYZUSDT")  # Bybit
    
    # Simulate Binance disconnection
    await binance.disconnect()
    
    # Switch from Binance to OKX
    await manager._switch_provider(Provider.OKX)
    
    # BTCUSDT and ETHUSDT should move to OKX
    # XYZUSDT should stay on Bybit
    assert manager._symbol_providers.get("BTCUSDT") == Provider.OKX
    assert manager._symbol_providers.get("ETHUSDT") == Provider.OKX
    assert manager._symbol_providers.get("XYZUSDT") == Provider.BYBIT


@pytest.mark.asyncio
async def test_sync_preserves_reference_counts(manager_with_mocks):
    """Test that sync() preserves existing reference counts."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Manually subscribe BTCUSDT with reference count
    await manager.subscribe("BTCUSDT")
    await manager.subscribe("BTCUSDT")
    
    initial_count = manager._subscriptions["BTCUSDT"]
    assert initial_count == 2
    
    # Run sync with same symbols
    await manager.sync({"BTCUSDT", "ETHUSDT"})
    
    # BTCUSDT count should be preserved
    assert manager._subscriptions["BTCUSDT"] == initial_count
    # ETHUSDT should be added with count 1
    assert manager._subscriptions["ETHUSDT"] == 1


@pytest.mark.asyncio
async def test_all_providers_fail_for_symbol(manager_with_mocks):
    """Test behavior when no provider supports a symbol."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Try to subscribe to a symbol none of them support
    with pytest.raises(RuntimeError, match="No provider supports FAKESYMBOL"):
        await manager.subscribe("FAKESYMBOL")
    
    # Symbol should not be in subscriptions
    assert "FAKESYMBOL" not in manager._subscriptions
    assert "FAKESYMBOL" not in manager._symbol_providers


@pytest.mark.asyncio
async def test_symbol_stays_on_assigned_provider(manager_with_mocks):
    """Test that a symbol with an assigned provider stays there if provider is connected."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe BTCUSDT (goes to Binance)
    await manager.subscribe("BTCUSDT")
    assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
    
    # Unsubscribe and re-subscribe
    await manager.unsubscribe("BTCUSDT")
    
    # Clear subscribe calls to track new subscription
    binance._subscribe_calls.clear()
    bybit._subscribe_calls.clear()
    
    # Re-subscribe - should go back to Binance (active provider)
    await manager.subscribe("BTCUSDT")
    
    assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
    assert "BTCUSDT" in binance._subscribe_calls
    assert "BTCUSDT" not in bybit._subscribe_calls


@pytest.mark.asyncio
async def test_sync_adds_and_removes_symbols(manager_with_mocks):
    """Test sync correctly adds missing and removes unused symbols."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe initial symbols
    await manager.subscribe("BTCUSDT")
    await manager.subscribe("ETHUSDT")
    
    # Sync with different set
    await manager.sync({"ETHUSDT", "XYZUSDT"})
    
    # BTCUSDT should be removed
    assert "BTCUSDT" not in manager._subscriptions
    assert "BTCUSDT" not in manager._symbol_providers
    
    # ETHUSDT should remain
    assert "ETHUSDT" in manager._subscriptions
    
    # XYZUSDT should be added
    assert "XYZUSDT" in manager._subscriptions
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT


@pytest.mark.asyncio
async def test_provider_disconnected_during_subscribe(manager_with_mocks):
    """Test behavior when assigned provider is disconnected during subscribe."""
    manager, binance, bybit, okx = manager_with_mocks
    
    # Subscribe BTCUSDT to Binance
    await manager.subscribe("BTCUSDT")
    await manager.unsubscribe("BTCUSDT")
    
    # Disconnect Binance
    await binance.disconnect()
    
    # Try to subscribe again - should fall back to Bybit
    await manager.subscribe("BTCUSDT")
    
    # Should be on Bybit now
    assert manager._symbol_providers["BTCUSDT"] == Provider.BYBIT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
