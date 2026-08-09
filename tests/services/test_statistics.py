"""
Unit tests for StatisticsService.

Tests all statistics calculations including edge cases and validation.
"""

import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from app.core.dto import SignalStatistics, TimeWindow
from app.database.enums import SignalStatus, TrackingStatus, CloseReason
from app.services.statistics import StatisticsService
from app.services.validation import ScoringValidationError


@pytest.fixture
def mock_uow():
    """Create a mock UnitOfWork."""
    uow = Mock()
    uow.session = AsyncMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    return uow


@pytest.fixture
def statistics_service(mock_uow):
    """Create StatisticsService with mocked dependencies."""
    return StatisticsService(mock_uow)


class TestStatisticsService:
    """Test StatisticsService functionality."""
    
    async def test_get_source_statistics_valid_source(self, statistics_service, mock_uow):
        """Test getting statistics for a valid source with data."""
        
        # Mock database responses
        mock_uow.session.scalar.side_effect = [
            10,  # total_signals
            8,   # completed_signals  
            2,   # active_signals
            6,   # tp_hit_count
            2,   # stop_loss_count
            0,   # cancelled_count
            0,   # expired_count
        ]
        
        mock_uow.session.scalars.return_value = [
            Decimal('5.25'), Decimal('3.75'), Decimal('-2.15'), 
            Decimal('8.50'), Decimal('1.20'), Decimal('-0.85')
        ]
        
        result = await statistics_service.get_source_statistics(1)
        
        assert isinstance(result, SignalStatistics)
        assert result.source_id == 1
        assert result.total_signals == 10
        assert result.completed_signals == 8
        assert result.active_signals == 2
        assert result.tp_hit_count == 6
        assert result.stop_loss_count == 2
        assert result.tp_hit_rate == Decimal('0.7500')  # 6/8
        assert result.stop_loss_rate == Decimal('0.2500')  # 2/8
        
    async def test_get_source_statistics_zero_signals(self, statistics_service, mock_uow):
        """Test getting statistics for source with no signals."""
        
        # Mock database responses for zero signals
        mock_uow.session.scalar.side_effect = [0] * 7
        mock_uow.session.scalars.return_value = []
        
        result = await statistics_service.get_source_statistics(1)
        
        assert result.total_signals == 0
        assert result.completed_signals == 0
        assert result.tp_hit_rate == Decimal('0.0000')
        assert result.stop_loss_rate == Decimal('0.0000')
        assert result.total_profit == Decimal('0.0000')
        
    async def test_get_source_statistics_zero_completed(self, statistics_service, mock_uow):
        """Test getting statistics for source with no completed signals."""
        
        mock_uow.session.scalar.side_effect = [
            5,   # total_signals
            0,   # completed_signals  
            5,   # active_signals
            0,   # tp_hit_count
            0,   # stop_loss_count
            0,   # cancelled_count
            0,   # expired_count
        ]
        
        mock_uow.session.scalars.return_value = []
        
        result = await statistics_service.get_source_statistics(1)
        
        assert result.total_signals == 5
        assert result.completed_signals == 0
        assert result.active_signals == 5
        assert result.tp_hit_rate == Decimal('0.0000')  # No completed signals
        assert result.stop_loss_rate == Decimal('0.0000')
        
    async def test_get_source_statistics_with_time_window(self, statistics_service, mock_uow):
        """Test statistics calculation with time window filter."""
        
        time_window = TimeWindow.last_48h()
        
        mock_uow.session.scalar.side_effect = [3, 2, 1, 1, 1, 0, 0]
        mock_uow.session.scalars.return_value = [Decimal('2.5'), Decimal('-1.0')]
        
        result = await statistics_service.get_source_statistics(1, time_window)
        
        assert result.total_signals == 3
        assert result.completed_signals == 2
        
    async def test_calculate_confidence_score(self, statistics_service):
        """Test confidence score calculation."""
        
        # Test various signal counts
        assert await statistics_service.calculate_confidence_score(0) == 0.0
        assert await statistics_service.calculate_confidence_score(1) == 0.1
        assert await statistics_service.calculate_confidence_score(25) == 0.5
        assert await statistics_service.calculate_confidence_score(100) == 1.0
        assert await statistics_service.calculate_confidence_score(400) == 1.0  # Capped at 1.0
        
    async def test_get_profit_percentiles(self, statistics_service, mock_uow):
        """Test profit percentiles calculation for normalization."""
        
        # Mock active sources
        mock_source1 = Mock()
        mock_source1.id = 1
        mock_source2 = Mock()  
        mock_source2.id = 2
        
        mock_uow.signal_sources = Mock()
        mock_uow.signal_sources.active = AsyncMock(return_value=[mock_source1, mock_source2])
        
        # Mock statistics for each source
        with patch.object(statistics_service, 'get_source_statistics') as mock_get_stats:
            mock_get_stats.side_effect = [
                SignalStatistics(
                    source_id=1, total_signals=10, completed_signals=8, active_signals=2,
                    tp_hit_count=6, stop_loss_count=2, cancelled_count=0, expired_count=0,
                    tp_hit_rate=Decimal('0.75'), stop_loss_rate=Decimal('0.25'),
                    total_profit=Decimal('15.50'), average_profit=Decimal('1.94'),
                    best_profit=Decimal('8.25'), worst_profit=Decimal('-2.10'),
                    profitable_signal_count=6, losing_signal_count=2
                ),
                SignalStatistics(
                    source_id=2, total_signals=5, completed_signals=5, active_signals=0,
                    tp_hit_count=3, stop_loss_count=2, cancelled_count=0, expired_count=0,
                    tp_hit_rate=Decimal('0.60'), stop_loss_rate=Decimal('0.40'),
                    total_profit=Decimal('8.75'), average_profit=Decimal('1.75'),
                    best_profit=Decimal('5.20'), worst_profit=Decimal('-1.50'),
                    profitable_signal_count=3, losing_signal_count=2
                )
            ]
            
            result = await statistics_service.get_profit_percentiles()
            
            assert 'total_profits' in result
            assert 'average_profits' in result  
            assert 'best_profits' in result
            assert len(result['total_profits']) == 2
            assert Decimal('15.50') in result['total_profits']
            assert Decimal('8.75') in result['total_profits']
            
    async def test_invalid_source_id(self, statistics_service):
        """Test validation of invalid source ID."""
        
        with pytest.raises(ScoringValidationError, match="Invalid source ID"):
            await statistics_service.get_source_statistics(-1)
            
        with pytest.raises(ScoringValidationError, match="Invalid source ID"):
            await statistics_service.get_source_statistics(0)
            
    async def test_invalid_time_window(self, statistics_service):
        """Test validation of invalid time window."""
        
        invalid_window = TimeWindow("invalid", -5)  # Negative hours
        
        with pytest.raises(ScoringValidationError, match="Invalid time window"):
            await statistics_service.get_source_statistics(1, invalid_window)
            
    async def test_data_quality_warnings(self, statistics_service, mock_uow):
        """Test detection of data quality issues."""
        
        # Mock data that should trigger warnings
        mock_uow.session.scalar.side_effect = [
            3,   # total_signals (low sample size warning)
            3,   # completed_signals
            0,   # active_signals  
            3,   # tp_hit_count (100% TP rate warning)
            0,   # stop_loss_count
            0,   # cancelled_count
            0,   # expired_count
        ]
        
        mock_uow.session.scalars.return_value = [
            Decimal('2000.0'),  # Extreme profit (warning)
            Decimal('5.0'), 
            Decimal('3.0')
        ]
        
        result = await statistics_service.get_source_statistics(1)
        
        # Should still return valid statistics despite warnings
        assert result.total_signals == 3
        assert result.tp_hit_rate == Decimal('1.0000')  # 100%
        assert result.best_profit == Decimal('2000.0')  # Extreme value
        
    async def test_concurrent_statistics_requests(self, statistics_service, mock_uow):
        """Test handling of concurrent statistics requests."""
        
        import asyncio
        
        # Mock responses
        mock_uow.session.scalar.side_effect = [5, 4, 1, 3, 1, 0, 0] * 3  # For 3 concurrent calls
        mock_uow.session.scalars.return_value = [Decimal('2.5')] * 4
        
        # Make concurrent requests
        tasks = [
            statistics_service.get_source_statistics(i)
            for i in [1, 2, 3]
        ]
        
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 3
        for result in results:
            assert isinstance(result, SignalStatistics)
            
    async def test_build_time_filter(self, statistics_service):
        """Test time filter building."""
        
        # Test None time window
        result = statistics_service._build_time_filter(None)
        assert result is None
        
        # Test time window with hours
        time_window = TimeWindow.last_48h()
        result = statistics_service._build_time_filter(time_window)
        assert isinstance(result, datetime)
        assert result < datetime.now(timezone.utc)
        
        # Test all-time window
        all_time = TimeWindow.all_time()
        result = statistics_service._build_time_filter(all_time)
        assert result is None
        
    async def test_calculate_rate_edge_cases(self, statistics_service):
        """Test rate calculation edge cases."""
        
        # Division by zero
        assert statistics_service._calculate_rate(5, 0) == Decimal('0.0000')
        
        # Normal case
        assert statistics_service._calculate_rate(3, 10) == Decimal('0.3000')
        
        # All hits
        assert statistics_service._calculate_rate(10, 10) == Decimal('1.0000')
        
        # Zero hits
        assert statistics_service._calculate_rate(0, 10) == Decimal('0.0000')
        
    async def test_database_error_handling(self, statistics_service, mock_uow):
        """Test handling of database errors."""
        
        # Simulate database error
        mock_uow.session.scalar.side_effect = Exception("Database connection failed")
        
        with pytest.raises(ScoringValidationError, match="Failed to calculate statistics"):
            await statistics_service.get_source_statistics(1)
            
    async def test_get_all_sources_statistics(self, statistics_service, mock_uow):
        """Test getting statistics for all sources."""
        
        # Mock active sources
        mock_source1 = Mock()
        mock_source1.id = 1
        mock_source2 = Mock()
        mock_source2.id = 2
        
        mock_uow.signal_sources = Mock()
        mock_uow.signal_sources.active = AsyncMock(return_value=[mock_source1, mock_source2])
        
        # Mock individual statistics calls
        with patch.object(statistics_service, 'get_source_statistics') as mock_get_stats:
            mock_stats = Mock(spec=SignalStatistics)
            mock_get_stats.return_value = mock_stats
            
            result = await statistics_service.get_all_sources_statistics()
            
            assert len(result) == 2
            assert 1 in result
            assert 2 in result
            assert mock_get_stats.call_count == 2