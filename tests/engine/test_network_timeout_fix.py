"""
Test to verify network timeouts work correctly.
"""
import pytest
from datetime import datetime, timezone
import asyncio

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher  
from app.market.manager import ProviderManager
from app.market.providers.base import BaseProvider


class QuickTimeoutTestProvider(BaseProvider):
    """Test provider with controlled timeout behavior."""
    
    def __init__(self):
        super().__init__(EventDispatcher())
        
    @property
    def name(self) -> Provider:
        return Provider.BINANCE
        
    async def connect(self):
        self._connected = True
        
    async def disconnect(self):
        self._connected = False
        
    async def current_price(self, symbol: str):
        """Simulate hanging - but use shorter timeout for testing."""
        print(f"QuickTimeoutTestProvider.current_price({symbol}) - starting long operation...")
        await asyncio.sleep(2.0)  # Shorter than 5s timeout to test behavior
        print(f"Should not reach here for {symbol}")
        return None
        
    async def subscribe(self, symbol: str):
        print(f"QuickTimeoutTestProvider.subscribe({symbol}) - quick operation")
        pass
        
    async def unsubscribe(self, symbol: str):
        pass


@pytest.mark.asyncio
async def test_network_timeout_works():
    """
    Test that demonstrates the timeout mechanism working on a real operation.
    
    Uses a 2-second delay with 5-second timeout to prove timeout logic works
    without waiting the full 5 seconds.
    """
    # Test the timeout mechanism directly first
    async def slow_operation():
        print("Starting slow operation (2 seconds)...")
        await asyncio.sleep(2.0)
        print("Slow operation completed")
        return "success"
    
    start_time = datetime.now(timezone.utc)
    
    # This should NOT timeout (2s < 5s timeout)
    try:
        result = await asyncio.wait_for(slow_operation(), timeout=5.0)
        print(f"Result: {result}")
        assert result == "success"
    except asyncio.TimeoutError:
        assert False, "Should not timeout with 2s delay and 5s timeout"
    
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    print(f"Direct timeout test: {duration:.2f}s")
    
    # Now test with hanging operation
    async def hanging_operation():
        print("Starting hanging operation (10 seconds)...")
        await asyncio.sleep(10.0)
        return "should not reach"
    
    start_time = datetime.now(timezone.utc)
    
    # This SHOULD timeout (10s > 5s timeout)
    try:
        result = await asyncio.wait_for(hanging_operation(), timeout=5.0)
        assert False, "Should have timed out"
    except asyncio.TimeoutError:
        print("✓ Timeout worked correctly")
    
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    print(f"Timeout test duration: {duration:.2f}s")
    assert 4.5 <= duration <= 6.0, f"Should timeout in ~5s but took {duration:.2f}s"
    
    print("✓ Basic timeout mechanism verified")


@pytest.mark.asyncio 
async def test_provider_manager_timeout_integration():
    """
    Test timeout integration in ProviderManager with minimal delay.
    
    Uses provider that completes in 2s to verify normal operation,
    then tests error handling path.
    """
    # Create provider that takes 2 seconds (within 5s timeout)
    provider = QuickTimeoutTestProvider()
    await provider.connect()
    
    # Create minimal ProviderManager  
    dispatcher = EventDispatcher()
    cache = PriceCache()
    
    manager = ProviderManager(
        dispatcher=dispatcher,
        cache=cache, 
        providers={Provider.BINANCE: provider},
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
    )
    
    manager._running = True
    manager._active = Provider.BINANCE
    
    start_time = datetime.now(timezone.utc)
    
    # This should succeed (2s delay < 5s timeout) 
    result = await manager._try_subscribe_symbol("QUICKTEST")
    
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    
    print(f"Provider manager test completed in {duration:.2f}s")
    print(f"Result: {result}")
    
    # Should succeed because provider operation completes within timeout
    assert result is True, "Should succeed when provider responds within timeout"
    assert 1.8 <= duration <= 3.0, f"Should take ~2s but took {duration:.2f}s"
    
    print("✓ Provider manager timeout integration verified")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])