import pytest
import asyncio
from unittest.mock import AsyncMock, Mock
from datetime import datetime, timezone
from decimal import Decimal

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.market.manager import ProviderManager
from app.market.providers.base import BaseProvider


class MockProvider(BaseProvider):
    def __init__(self, name: Provider, cache: PriceCache):
        super().__init__(cache, polling_interval=0.1)
        self._name = name
        self._mock_consecutive_misses = {}
        self._mock_tickers = []
        
    @property
    def name(self) -> Provider:
        return self._name
        
    async def fetch_all_tickers(self) -> list[PriceTick]:
        return self._mock_tickers.copy()
    
    def set_mock_consecutive_misses(self, symbol: str, count: int):
        self._mock_consecutive_misses[symbol] = count
        
    def get_consecutive_misses(self, symbol: str) -> int:
        return self._mock_consecutive_misses.get(symbol, 0)
    
    def set_mock_tickers(self, tickers: list[PriceTick]):
        self._mock_tickers = tickers


@pytest.fixture
async def manager_setup():
    cache = PriceCache()
    
    binance = MockProvider(Provider.BINANCE, cache)
    bybit = MockProvider(Provider.BYBIT, cache) 
    okx = MockProvider(Provider.OKX, cache)
    
    providers = {
        Provider.BINANCE: binance,
        Provider.BYBIT: bybit,
        Provider.OKX: okx,
    }
    
    manager = ProviderManager(
        cache=cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
        consecutive_miss_threshold=2,
        check_interval=0.1,  # Fast for testing
    )
    
    yield manager, providers
    
    await manager.stop()


class TestProviderManager:
    async def test_symbol_failover_binance_to_bybit_to_okx(self, manager_setup):
        """Test that a symbol switches Binance -> Bybit -> OKX on consecutive misses"""
        manager, providers = manager_setup
        
        # Start manager
        await manager.start()
        
        # Add a symbol
        await manager.sync({"BTCUSDT"})
        
        # Initially on Binance
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
        
        # Simulate Binance failures (2 misses = threshold)
        providers[Provider.BINANCE].set_mock_consecutive_misses("BTCUSDT", 2)
        
        # Wait for failover check
        await asyncio.sleep(0.2)
        
        # Should switch to Bybit
        assert manager._symbol_providers["BTCUSDT"] == Provider.BYBIT
        
        # Simulate Bybit failures  
        providers[Provider.BYBIT].set_mock_consecutive_misses("BTCUSDT", 2)
        
        # Wait for failover check
        await asyncio.sleep(0.2)
        
        # Should switch to OKX (disaster)
        assert manager._symbol_providers["BTCUSDT"] == Provider.OKX
        
        # Simulate OKX failures - should stay on OKX (no further fallback)
        providers[Provider.OKX].set_mock_consecutive_misses("BTCUSDT", 2)
        
        # Wait for failover check
        await asyncio.sleep(0.2)
        
        # Should stay on OKX
        assert manager._symbol_providers["BTCUSDT"] == Provider.OKX

    async def test_recovery_switches_back_to_primary(self, manager_setup):
        """Test that recovery (primary_misses == 0 while not on primary) switches back to Binance"""
        manager, providers = manager_setup
        
        await manager.start()
        await manager.sync({"BTCUSDT"})
        
        # Force symbol to Bybit
        manager._symbol_providers["BTCUSDT"] = Provider.BYBIT
        manager._push_symbols_to_providers()
        
        # Simulate Binance recovery (0 misses)
        providers[Provider.BINANCE].set_mock_consecutive_misses("BTCUSDT", 0)
        providers[Provider.BYBIT].set_mock_consecutive_misses("BTCUSDT", 0)  # Bybit also working
        
        # Wait for recovery check
        await asyncio.sleep(0.2)
        
        # Should switch back to Binance (primary)
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE

    async def test_binance_gets_full_symbol_set_others_get_subset(self, manager_setup):
        """Test critical asymmetry: Binance always gets ALL symbols, others only get their routed subset"""
        manager, providers = manager_setup
        
        await manager.start()
        
        # Add multiple symbols
        await manager.sync({"BTCUSDT", "ETHUSDT", "ADAUSDT"})
        
        # Force some symbols to different providers
        manager._symbol_providers["BTCUSDT"] = Provider.BINANCE  # On primary
        manager._symbol_providers["ETHUSDT"] = Provider.BYBIT    # On fallback
        manager._symbol_providers["ADAUSDT"] = Provider.OKX      # On disaster
        manager._push_symbols_to_providers()
        
        # Verify Binance gets ALL symbols (for recovery detection)
        assert providers[Provider.BINANCE]._required_symbols == {"BTCUSDT", "ETHUSDT", "ADAUSDT"}
        
        # Verify others only get their routed symbols
        assert providers[Provider.BYBIT]._required_symbols == {"ETHUSDT"}
        assert providers[Provider.OKX]._required_symbols == {"ADAUSDT"}

    async def test_sync_adds_removes_symbols_correctly(self, manager_setup):
        """Test that sync() correctly adds new symbols and removes stale ones"""
        manager, providers = manager_setup
        
        await manager.start()
        
        # Initial sync
        await manager.sync({"BTCUSDT", "ETHUSDT"})
        
        assert "BTCUSDT" in manager._symbol_providers
        assert "ETHUSDT" in manager._symbol_providers
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE  # New symbols start on primary
        assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE
        
        # Add new symbol, remove one
        await manager.sync({"BTCUSDT", "ADAUSDT"})
        
        assert "BTCUSDT" in manager._symbol_providers  # Kept
        assert "ADAUSDT" in manager._symbol_providers  # Added (starts on primary)
        assert "ETHUSDT" not in manager._symbol_providers  # Removed
        assert manager._symbol_providers["ADAUSDT"] == Provider.BINANCE

    async def test_new_symbols_always_start_on_primary(self, manager_setup):
        """Test that new symbols always start on primary regardless of current provider states"""
        manager, providers = manager_setup
        
        await manager.start()
        
        # Add initial symbol
        await manager.sync({"BTCUSDT"}) 
        
        # Force it to disaster provider
        manager._symbol_providers["BTCUSDT"] = Provider.OKX
        manager._push_symbols_to_providers()
        
        # Add new symbol
        await manager.sync({"BTCUSDT", "ETHUSDT"})
        
        # New symbol should start on primary, existing stays where it was
        assert manager._symbol_providers["BTCUSDT"] == Provider.OKX      # Unchanged
        assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE  # New -> primary

    async def test_concurrent_sync_and_check_operations_are_safe(self, manager_setup):
        """Test that concurrent sync() and _check_and_switch() operations don't cause race conditions"""
        manager, providers = manager_setup
        
        await manager.start()
        await manager.sync({"BTCUSDT", "ETHUSDT"})
        
        async def rapid_sync():
            for i in range(10):
                await manager.sync({f"SYM{i}USDT"})
                await asyncio.sleep(0.01)
        
        async def rapid_check():
            for i in range(10):
                await manager._check_and_switch()
                await asyncio.sleep(0.01)
        
        # Run both concurrently - should not crash
        await asyncio.gather(rapid_sync(), rapid_check())
        
        # Verify state is consistent
        assert len(manager._symbol_providers) > 0
        for symbol, provider in manager._symbol_providers.items():
            assert provider in [Provider.BINANCE, Provider.BYBIT, Provider.OKX]

    async def test_empty_required_symbols_is_handled_gracefully(self, manager_setup):
        """Test that providers handle empty symbol sets without crashing"""
        manager, providers = manager_setup
        
        await manager.start()
        
        # Sync with empty set
        await manager.sync(set())
        
        # All providers should have empty symbol sets
        assert providers[Provider.BINANCE]._required_symbols == set()
        assert providers[Provider.BYBIT]._required_symbols == set()
        assert providers[Provider.OKX]._required_symbols == set()
        
        # Manager state should be empty
        assert manager._symbol_providers == {}
        assert manager._required_symbols == set()

    async def test_failover_threshold_boundary_conditions(self, manager_setup):
        """Test failover exactly at threshold vs just under threshold"""
        manager, providers = manager_setup
        
        await manager.start()
        await manager.sync({"BTCUSDT"})
        
        # Set misses to just under threshold (threshold = 2)
        providers[Provider.BINANCE].set_mock_consecutive_misses("BTCUSDT", 1)
        
        # Wait for check - should NOT switch
        await asyncio.sleep(0.2)
        assert manager._symbol_providers["BTCUSDT"] == Provider.BINANCE
        
        # Set misses to exactly threshold
        providers[Provider.BINANCE].set_mock_consecutive_misses("BTCUSDT", 2)
        
        # Wait for check - should switch
        await asyncio.sleep(0.2)
        assert manager._symbol_providers["BTCUSDT"] == Provider.BYBIT

    async def test_multiple_symbols_independent_failover(self, manager_setup):
        """Test that symbols fail over independently of each other"""
        manager, providers = manager_setup
        
        await manager.start()
        await manager.sync({"BTCUSDT", "ETHUSDT"})
        
        # Only BTCUSDT fails on Binance
        providers[Provider.BINANCE].set_mock_consecutive_misses("BTCUSDT", 2)
        providers[Provider.BINANCE].set_mock_consecutive_misses("ETHUSDT", 0)
        
        await asyncio.sleep(0.2)
        
        # Only BTCUSDT should switch, ETHUSDT stays on Binance
        assert manager._symbol_providers["BTCUSDT"] == Provider.BYBIT
        assert manager._symbol_providers["ETHUSDT"] == Provider.BINANCE
        
        # Verify provider symbol assignments reflect this
        assert "BTCUSDT" in providers[Provider.BINANCE]._required_symbols  # Binance monitors all
        assert "ETHUSDT" in providers[Provider.BINANCE]._required_symbols  # Binance monitors all
        assert "BTCUSDT" in providers[Provider.BYBIT]._required_symbols    # Bybit gets routed symbol
        assert "ETHUSDT" not in providers[Provider.BYBIT]._required_symbols # Bybit doesn't get non-routed
        assert providers[Provider.OKX]._required_symbols == set()          # OKX gets nothing