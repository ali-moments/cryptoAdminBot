"""
Integration test for per-symbol provider selection with real provider behavior.

This test validates the complete flow:
1. Multiple symbols distributed across providers
2. Provider failover affecting only impacted symbols
3. Recovery after restart
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


class IntegrationMockProvider(BaseProvider):
    """Mock provider for integration testing with realistic behavior."""
    
    def __init__(self, dispatcher, name: Provider, supported_symbols: set[str]):
        super().__init__(dispatcher)
        self._name = name
        self._supported_symbols = supported_symbols
        self._subscriptions: dict[str, int] = {}
    
    @property
    def name(self) -> Provider:
        return self._name
    
    async def connect(self) -> None:
        self._connected = True
    
    async def disconnect(self) -> None:
        self._connected = False
        # Simulate clearing subscriptions on disconnect
        self._subscriptions.clear()
    
    async def subscribe(self, symbol: str) -> None:
        if not self._connected:
            raise RuntimeError(f"{self._name.value} is not connected")
        count = self._subscriptions.get(symbol, 0)
        self._subscriptions[symbol] = count + 1
    
    async def unsubscribe(self, symbol: str) -> None:
        count = self._subscriptions.get(symbol)
        if count and count == 1:
            del self._subscriptions[symbol]
        elif count:
            self._subscriptions[symbol] = count - 1
    
    async def current_price(self, symbol: str) -> PriceTick:
        if symbol not in self._supported_symbols:
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


@pytest.mark.asyncio
async def test_complete_integration_scenario():
    """
    Complete integration test simulating real-world scenario:
    1. Subscribe symbols to different providers based on availability
    2. Simulate provider failure
    3. Verify only affected symbols moved
    4. Simulate restart and recovery
    """
    # Setup
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    # Create providers with realistic symbol support
    # Binance: BTC, ETH (most common)
    # Bybit: BTC, ETH, XYZ (more altcoins)
    # OKX: BTC only
    binance = IntegrationMockProvider(dispatcher, Provider.BINANCE, {"BTCUSDT", "ETHUSDT"})
    bybit = IntegrationMockProvider(dispatcher, Provider.BYBIT, {"BTCUSDT", "ETHUSDT", "XYZUSDT"})
    okx = IntegrationMockProvider(dispatcher, Provider.OKX, {"BTCUSDT"})
    
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
    
    # Start manager
    await binance.connect()
    await bybit.connect()
    await okx.connect()
    manager._active = Provider.BINANCE
    manager._running = True
    
    # Phase 1: Subscribe symbols - should distribute across providers
    print("\n=== Phase 1: Initial Subscription ===")
    await manager.subscribe("BTCUSDT")   # Should go to Binance (primary)
    await manager.subscribe("ETHUSDT")   # Should go to Binance (primary)
    await manager.subscribe("XYZUSDT")   # Should go to Bybit (Binance doesn't support)
    
    # Verify distribution
    assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
    assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT
    assert len(binance._subscriptions) == 2  # BTC, ETH
    assert len(bybit._subscriptions) == 1    # XYZ
    print(f"✓ Symbols distributed: Binance={list(binance._subscriptions.keys())}, Bybit={list(bybit._subscriptions.keys())}")
    
    # Phase 2: Simulate Binance failure
    print("\n=== Phase 2: Binance Failover ===")
    await binance.disconnect()
    
    # Switch from Binance to OKX
    success = await manager._switch_provider(Provider.OKX)
    assert success
    
    # Verify only Binance symbols moved
    assert manager._symbol_providers["BTCUSDT"] == Provider.OKX  # Moved from Binance to OKX
    assert manager._symbol_providers["ETHUSDT"] == Provider.OKX  # Moved from Binance to OKX
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT  # Stayed on Bybit
    assert len(okx._subscriptions) == 2      # BTC, ETH moved here
    assert len(bybit._subscriptions) == 1    # XYZ still here
    print(f"✓ After failover: OKX={list(okx._subscriptions.keys())}, Bybit={list(bybit._subscriptions.keys())}")
    
    # Phase 3: Simulate application restart
    print("\n=== Phase 3: Restart Recovery ===")
    
    # Clear runtime state (simulating restart)
    manager._subscriptions.clear()
    manager._symbol_providers.clear()
    
    # Reconnect providers
    await binance.connect()
    manager._active = Provider.BINANCE
    
    # Use sync to recover subscriptions (like SubscriptionManager would)
    required_symbols = {"BTCUSDT", "ETHUSDT", "XYZUSDT"}
    await manager.sync(required_symbols)
    
    # Verify recovery: symbols should go back to their preferred providers
    assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE  # Back to primary
    assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE  # Back to primary
    assert manager._symbol_providers["XYZUSDT"] == Provider.BYBIT    # Stays on Bybit (not on Binance)
    assert len(manager._subscriptions) == 3
    print(f"✓ After restart: Binance={list(binance._subscriptions.keys())}, Bybit={list(bybit._subscriptions.keys())}")
    
    # Phase 4: Verify reference counting still works
    print("\n=== Phase 4: Reference Counting ===")
    await manager.subscribe("BTCUSDT")  # Increment ref count
    assert manager._subscriptions["BTCUSDT"] == 2
    
    await manager.unsubscribe("BTCUSDT")  # Decrement
    assert manager._subscriptions["BTCUSDT"] == 1
    assert "BTCUSDT" in binance._subscriptions  # Still subscribed at provider level
    
    await manager.unsubscribe("BTCUSDT")  # Final unsubscribe
    assert "BTCUSDT" not in manager._subscriptions
    assert "BTCUSDT" not in binance._subscriptions
    print("✓ Reference counting works correctly")
    
    # Cleanup
    manager._running = False
    print("\n=== Integration Test Complete ===")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
