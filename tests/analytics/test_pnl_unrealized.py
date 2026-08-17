import pytest
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import Mock, AsyncMock

from app.analytics.pnl import PnlAnalytics
from app.market.cache import PriceCache
from app.market.dto import PriceTick
from app.database.enums import Direction, Provider
from app.database.uow import UnitOfWork


@pytest.fixture
def mock_uow():
    return Mock(spec=UnitOfWork)


@pytest.fixture
def mock_price_cache():
    return Mock(spec=PriceCache)


@pytest.fixture
def pnl_analytics(mock_uow, mock_price_cache):
    return PnlAnalytics(mock_uow, mock_price_cache)


@pytest.fixture
def mock_tracking():
    tracking = Mock()
    tracking.actual_entry_price = Decimal("100.0")
    tracking.signal = Mock()
    tracking.signal.symbol = "BTCUSDT"
    tracking.signal.direction = Direction.LONG
    return tracking


class TestCalculateUnrealizedPnl:
    """Test _calculate_unrealized_pnl method specifically for the fixed bug."""

    @pytest.mark.asyncio
    async def test_missing_entry_price_returns_zero(self, pnl_analytics, mock_tracking):
        """Test that missing entry price returns Decimal('0')."""
        mock_tracking.actual_entry_price = None
        
        result = await pnl_analytics._calculate_unrealized_pnl(mock_tracking)
        
        assert result == Decimal("0")

    @pytest.mark.asyncio
    async def test_missing_price_tick_returns_zero(self, pnl_analytics, mock_tracking, mock_price_cache):
        """Test that missing PriceTick returns Decimal('0')."""
        # PriceCache.get() returns None (no price available)
        mock_price_cache.get.return_value = None
        
        result = await pnl_analytics._calculate_unrealized_pnl(mock_tracking)
        
        mock_price_cache.get.assert_called_once_with("BTCUSDT")
        assert result == Decimal("0")

    @pytest.mark.asyncio
    async def test_long_position_profit_calculation(self, pnl_analytics, mock_tracking, mock_price_cache):
        """Test unrealized PNL calculation for LONG position in profit."""
        # Entry: $100, Current: $110 = 10% profit
        mock_tracking.signal.direction = Direction.LONG
        mock_tracking.actual_entry_price = Decimal("100.0")
        
        price_tick = PriceTick(
            symbol="BTCUSDT",
            price=Decimal("110.0"),
            provider=Provider.BINANCE,
            timestamp=datetime.now(timezone.utc)
        )
        mock_price_cache.get.return_value = price_tick
        
        result = await pnl_analytics._calculate_unrealized_pnl(mock_tracking)
        
        mock_price_cache.get.assert_called_once_with("BTCUSDT")
        assert result == Decimal("10.00")

    @pytest.mark.asyncio
    async def test_long_position_loss_calculation(self, pnl_analytics, mock_tracking, mock_price_cache):
        """Test unrealized PNL calculation for LONG position in loss."""
        # Entry: $100, Current: $90 = -10% loss
        mock_tracking.signal.direction = Direction.LONG
        mock_tracking.actual_entry_price = Decimal("100.0")
        
        price_tick = PriceTick(
            symbol="BTCUSDT",
            price=Decimal("90.0"),
            provider=Provider.BINANCE,
            timestamp=datetime.now(timezone.utc)
        )
        mock_price_cache.get.return_value = price_tick
        
        result = await pnl_analytics._calculate_unrealized_pnl(mock_tracking)
        
        mock_price_cache.get.assert_called_once_with("BTCUSDT")
        assert result == Decimal("-10.00")

    @pytest.mark.asyncio
    async def test_short_position_profit_calculation(self, pnl_analytics, mock_tracking, mock_price_cache):
        """Test unrealized PNL calculation for SHORT position in profit."""
        # Entry: $100, Current: $90 = 10% profit for SHORT
        mock_tracking.signal.direction = Direction.SHORT
        mock_tracking.actual_entry_price = Decimal("100.0")
        
        price_tick = PriceTick(
            symbol="BTCUSDT",
            price=Decimal("90.0"),
            provider=Provider.BINANCE,
            timestamp=datetime.now(timezone.utc)
        )
        mock_price_cache.get.return_value = price_tick
        
        result = await pnl_analytics._calculate_unrealized_pnl(mock_tracking)
        
        mock_price_cache.get.assert_called_once_with("BTCUSDT")
        assert result == Decimal("10.00")

    @pytest.mark.asyncio
    async def test_short_position_loss_calculation(self, pnl_analytics, mock_tracking, mock_price_cache):
        """Test unrealized PNL calculation for SHORT position in loss."""
        # Entry: $100, Current: $110 = -10% loss for SHORT
        mock_tracking.signal.direction = Direction.SHORT
        mock_tracking.actual_entry_price = Decimal("100.0")
        
        price_tick = PriceTick(
            symbol="BTCUSDT",
            price=Decimal("110.0"),
            provider=Provider.BINANCE,
            timestamp=datetime.now(timezone.utc)
        )
        mock_price_cache.get.return_value = price_tick
        
        result = await pnl_analytics._calculate_unrealized_pnl(mock_tracking)
        
        mock_price_cache.get.assert_called_once_with("BTCUSDT")
        assert result == Decimal("-10.00")