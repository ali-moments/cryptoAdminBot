import pytest
import asyncio
from unittest.mock import AsyncMock, Mock
from datetime import datetime, timezone
from decimal import Decimal

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.market.providers.base import BaseProvider


class MockProvider(BaseProvider):
    @property
    def name(self) -> Provider:
        return Provider.BINANCE
    
    async def fetch_all_tickers(self) -> list[PriceTick]:
        # This will be mocked in tests
        return []


@pytest.fixture
async def provider():
    cache = PriceCache()
    provider = MockProvider(cache, polling_interval=0.1)  # Fast polling for tests
    yield provider
    await provider.stop_polling()


class TestBaseProvider:
    async def test_polling_loop_writes_to_cache_on_present_symbol(self, provider):
        """Test that polling loop correctly writes to cache when symbol is present"""
        # Setup
        mock_tick = PriceTick(
            symbol="BTCUSDT",
            price=Decimal("50000.0"),
            provider=Provider.BINANCE,
            timestamp=datetime.now(timezone.utc)
        )
        
        provider.fetch_all_tickers = AsyncMock(return_value=[mock_tick])
        provider.update_required_symbols({"BTCUSDT"})
        
        # Start polling and let it run once
        await provider.start_polling()
        await asyncio.sleep(0.2)  # Let it poll once
        await provider.stop_polling()
        
        # Verify cache was updated
        cached_tick = provider._cache.get("BTCUSDT")
        assert cached_tick is not None
        assert cached_tick.symbol == "BTCUSDT"
        assert cached_tick.price == Decimal("50000.0")
        
        # Verify miss counter reset
        assert provider.get_consecutive_misses("BTCUSDT") == 0

    async def test_polling_loop_increments_miss_counter_on_absent_symbol(self, provider):
        """Test that polling loop increments miss counter when symbol is absent"""
        # Setup - return tickers but not the required symbol
        other_tick = PriceTick(
            symbol="ETHUSDT",
            price=Decimal("3000.0"),
            provider=Provider.BINANCE,
            timestamp=datetime.now(timezone.utc)
        )
        
        provider.fetch_all_tickers = AsyncMock(return_value=[other_tick])
        provider.update_required_symbols({"BTCUSDT"})  # Require BTCUSDT but don't return it
        
        # Start polling and let it run twice
        await provider.start_polling()
        await asyncio.sleep(0.3)  # Let it poll twice
        await provider.stop_polling()
        
        # Verify cache was NOT updated for missing symbol
        cached_tick = provider._cache.get("BTCUSDT")
        assert cached_tick is None
        
        # Verify miss counter incremented
        assert provider.get_consecutive_misses("BTCUSDT") >= 1

    async def test_polling_loop_increments_miss_for_all_symbols_on_fetch_exception(self, provider):
        """Test that fetch exception increments miss counter for ALL required symbols"""
        # Setup
        provider.fetch_all_tickers = AsyncMock(side_effect=Exception("API error"))
        provider.update_required_symbols({"BTCUSDT", "ETHUSDT", "ADAUSDT"})
        
        # Start polling and let it run once
        await provider.start_polling()
        await asyncio.sleep(0.2)  # Let it poll once
        await provider.stop_polling()
        
        # Verify all symbols got miss increments
        assert provider.get_consecutive_misses("BTCUSDT") >= 1
        assert provider.get_consecutive_misses("ETHUSDT") >= 1
        assert provider.get_consecutive_misses("ADAUSDT") >= 1

    async def test_polling_loop_never_crashes_on_exception(self, provider):
        """Test that polling loop continues after exceptions"""
        call_count = 0
        
        async def failing_then_working():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("First call fails")
            return [PriceTick(
                symbol="BTCUSDT",
                price=Decimal("50000.0"),
                provider=Provider.BINANCE,
                timestamp=datetime.now(timezone.utc)
            )]
        
        provider.fetch_all_tickers = AsyncMock(side_effect=failing_then_working)
        provider.update_required_symbols({"BTCUSDT"})
        
        # Start polling and let it run multiple times
        await provider.start_polling()
        await asyncio.sleep(0.3)  # Let it poll multiple times
        await provider.stop_polling()
        
        # Verify it called fetch multiple times (didn't crash after first failure)
        assert call_count >= 2
        
        # Verify it eventually succeeded (cache should have data)
        cached_tick = provider._cache.get("BTCUSDT")
        assert cached_tick is not None

    async def test_update_required_symbols(self, provider):
        """Test that update_required_symbols correctly updates the symbol set"""
        # Initially empty
        assert provider._required_symbols == set()
        
        # Update with symbols
        symbols = {"BTCUSDT", "ETHUSDT"}
        provider.update_required_symbols(symbols)
        
        # Verify copy was made (not reference)
        assert provider._required_symbols == symbols
        symbols.add("ADAUSDT")
        assert "ADAUSDT" not in provider._required_symbols

    async def test_consecutive_misses_tracking(self, provider):
        """Test that consecutive misses are tracked correctly per symbol"""
        # Initially 0
        assert provider.get_consecutive_misses("BTCUSDT") == 0
        
        # Simulate misses
        provider._consecutive_misses["BTCUSDT"] = 3
        assert provider.get_consecutive_misses("BTCUSDT") == 3
        
        # Different symbol should still be 0
        assert provider.get_consecutive_misses("ETHUSDT") == 0