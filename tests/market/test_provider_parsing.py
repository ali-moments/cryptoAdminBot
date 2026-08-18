import pytest
import asyncio
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import aiohttp

from app.database.enums import Provider
from app.market.cache import PriceCache
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider


class TestBinanceProvider:
    async def test_fetch_all_tickers_parses_realistic_response(self):
        """Test Binance parsing with actual API response format"""
        cache = PriceCache()
        provider = BinanceProvider(cache, polling_interval=5.0)
        
        # Real Binance API response format (from Step 5 verification)
        mock_response = [
            {"symbol": "BTCUSDT", "price": "50000.12345", "time": 1787089920538},
            {"symbol": "ETHUSDT", "price": "3000.98765", "time": 1787089920539},
            {"symbol": "ADAUSDT", "price": "0.45123", "time": 1787089920540}
        ]
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            ticks = await provider.fetch_all_tickers()
            
            assert len(ticks) == 3
            
            btc_tick = next(tick for tick in ticks if tick.symbol == "BTCUSDT")
            assert btc_tick.price == Decimal("50000.12345")
            assert btc_tick.provider == Provider.BINANCE
            assert isinstance(btc_tick.timestamp, datetime)
            
            eth_tick = next(tick for tick in ticks if tick.symbol == "ETHUSDT")
            assert eth_tick.price == Decimal("3000.98765")
            assert eth_tick.provider == Provider.BINANCE

    async def test_binance_handles_empty_response(self):
        """Test Binance handles empty response gracefully"""
        cache = PriceCache()
        provider = BinanceProvider(cache, polling_interval=5.0)
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=[])
            mock_session.get = AsyncMock(return_value=mock_response_obj)
            
            ticks = await provider.fetch_all_tickers()
            assert ticks == []


class TestBybitProvider:
    async def test_fetch_all_tickers_parses_realistic_response(self):
        """Test Bybit parsing with actual API response format"""
        cache = PriceCache()
        provider = BybitProvider(cache, polling_interval=5.0)
        
        # Real Bybit API response format (from Step 5 verification) 
        mock_response = {
            "retCode": 0,
            "retMsg": "OK", 
            "result": {
                "category": "linear",
                "list": [
                    {"symbol": "BTCUSDT", "lastPrice": "50000.12"},
                    {"symbol": "ETHUSDT", "lastPrice": "3000.98"},
                    {"symbol": "ADAUSDT", "lastPrice": "0.4512"}
                ]
            }
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            ticks = await provider.fetch_all_tickers()
            
            assert len(ticks) == 3
            
            btc_tick = next(tick for tick in ticks if tick.symbol == "BTCUSDT")
            assert btc_tick.price == Decimal("50000.12")
            assert btc_tick.provider == Provider.BYBIT
            
            eth_tick = next(tick for tick in ticks if tick.symbol == "ETHUSDT")
            assert eth_tick.price == Decimal("3000.98")

    async def test_bybit_handles_error_response(self):
        """Test Bybit raises exception on API error response"""
        cache = PriceCache()
        provider = BybitProvider(cache, polling_interval=5.0)
        
        # Bybit error response
        mock_response = {
            "retCode": 10001,
            "retMsg": "Invalid request",
            "result": {}
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            with pytest.raises(RuntimeError, match="Bybit API error"):
                await provider.fetch_all_tickers()

    async def test_bybit_handles_empty_list(self):
        """Test Bybit handles empty ticker list"""
        cache = PriceCache()
        provider = BybitProvider(cache, polling_interval=5.0)
        
        mock_response = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "category": "linear", 
                "list": []
            }
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            ticks = await provider.fetch_all_tickers()
            assert ticks == []


class TestOKXProvider:
    async def test_fetch_all_tickers_parses_realistic_response(self):
        """Test OKX parsing with actual API response format"""
        cache = PriceCache()
        provider = OKXProvider(cache, polling_interval=5.0)
        
        # Real OKX API response format (from Step 5 verification)
        mock_response = {
            "code": "0",
            "msg": "",
            "data": [
                {"instType": "SWAP", "instId": "BTC-USDT-SWAP", "last": "50000.12"},
                {"instType": "SWAP", "instId": "ETH-USDT-SWAP", "last": "3000.98"}, 
                {"instType": "SWAP", "instId": "ADA-USDT-SWAP", "last": "0.4512"}
            ]
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            ticks = await provider.fetch_all_tickers()
            
            assert len(ticks) == 3
            
            # Verify symbol normalization: "BTC-USDT-SWAP" -> "BTCUSDT"
            btc_tick = next(tick for tick in ticks if tick.symbol == "BTCUSDT")
            assert btc_tick.price == Decimal("50000.12")
            assert btc_tick.provider == Provider.OKX
            
            # Verify other symbols normalized correctly
            symbols = {tick.symbol for tick in ticks}
            assert symbols == {"BTCUSDT", "ETHUSDT", "ADAUSDT"}

    async def test_okx_symbol_normalization_edge_cases(self):
        """Test OKX symbol normalization with various hyphen patterns"""
        cache = PriceCache()
        provider = OKXProvider(cache, polling_interval=5.0)
        
        # Test various instId formats that might appear in OKX responses
        mock_response = {
            "code": "0",
            "msg": "",
            "data": [
                {"instType": "SWAP", "instId": "BTC-USDT-SWAP", "last": "50000.12"},      # Normal case
                {"instType": "SWAP", "instId": "1000SHIB-USDT-SWAP", "last": "0.00001"},  # Token with numbers
                {"instType": "SWAP", "instId": "YGG-USDT-SWAP", "last": "0.15"},          # Short token name
                {"instType": "FUTURES", "instId": "BTC-USD-241225", "last": "49000.0"},   # Different quote/expiry
            ]
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            ticks = await provider.fetch_all_tickers()
            
            # Verify normalization: take first 2 parts, join without hyphen
            symbols = {tick.symbol for tick in ticks}
            expected_symbols = {"BTCUSDT", "1000SHIBUSDT", "YGGUSDT", "BTCUSD"}
            assert symbols == expected_symbols

    async def test_okx_handles_error_response(self):
        """Test OKX raises exception on API error response"""
        cache = PriceCache()
        provider = OKXProvider(cache, polling_interval=5.0)
        
        # OKX error response
        mock_response = {
            "code": "50001",
            "msg": "Invalid request",
            "data": []
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            with pytest.raises(RuntimeError, match="OKX API error"):
                await provider.fetch_all_tickers()

    async def test_okx_handles_empty_data(self):
        """Test OKX handles empty data array"""
        cache = PriceCache()
        provider = OKXProvider(cache, polling_interval=5.0)
        
        mock_response = {
            "code": "0",
            "msg": "",
            "data": []
        }
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(return_value=mock_response)
            mock_response_obj.raise_for_status = AsyncMock()
            mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response_obj)
            mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)
            
            ticks = await provider.fetch_all_tickers()
            assert ticks == []


class TestProviderErrorHandling:
    """Test error handling across all providers"""
    
    @pytest.mark.parametrize("provider_class", [BinanceProvider, BybitProvider, OKXProvider])
    async def test_network_timeout_handling(self, provider_class):
        """Test that network timeouts raise exceptions (handled by BaseProvider polling loop)"""
        cache = PriceCache()
        provider = provider_class(cache, polling_interval=5.0)
        
        with patch.object(provider, '_session') as mock_session:
            mock_session.get = AsyncMock(side_effect=asyncio.TimeoutError("Request timeout"))
            
            # Should raise exception (polling loop catches and handles)
            with pytest.raises(asyncio.TimeoutError):
                await provider.fetch_all_tickers()

    @pytest.mark.parametrize("provider_class", [BinanceProvider, BybitProvider, OKXProvider])  
    async def test_json_decode_error_handling(self, provider_class):
        """Test that JSON decode errors raise exceptions (handled by BaseProvider polling loop)"""
        cache = PriceCache()
        provider = provider_class(cache, polling_interval=5.0)
        
        with patch.object(provider, '_session') as mock_session:
            mock_response_obj = AsyncMock()
            mock_response_obj.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
            mock_session.get = AsyncMock(return_value=mock_response_obj)
            
            # Should raise exception (polling loop catches and handles)
            with pytest.raises(ValueError):
                await provider.fetch_all_tickers()