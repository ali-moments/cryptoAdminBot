"""
Unit tests for ScoringIntegrationService.

Tests orchestration of scoring updates and database integration.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from decimal import Decimal

from app.core.dto import ScoreBreakdown, TimeWindow
from app.services.scoring_integration import ScoringIntegrationService
from app.services.validation import ScoringValidationError


@pytest.fixture
def mock_uow():
    """Create a mock UnitOfWork."""
    uow = Mock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.commit = AsyncMock()
    uow.rollback = AsyncMock()
    
    # Mock repositories
    uow.signal_sources = Mock()
    uow.signal_sources.active = AsyncMock()
    uow.signal_sources.update_score = AsyncMock()
    uow.signal_sources.update_statistics = AsyncMock()
    uow.signal_sources.batch_update_scores = AsyncMock()
    uow.signal_sources.get = AsyncMock()
    
    return uow


@pytest.fixture
def integration_service(mock_uow):
    """Create ScoringIntegrationService with mocked dependencies."""
    return ScoringIntegrationService(mock_uow)


@pytest.fixture
def sample_score_breakdown():
    """Create sample score breakdown for testing."""
    return ScoreBreakdown(
        score=750,
        display_score=7.50,
        tp_hit_rate_score=0.8,
        profitability_score=0.7,
        average_profit_score=0.6,
        best_profit_score=0.9,
        stop_loss_score=0.8,
        confidence_score=0.85,
        tp_hit_rate=Decimal('0.8'),
        stop_loss_rate=Decimal('0.2'),
        total_profit=Decimal('25.0'),
        average_profit=Decimal('1.5'),
        best_profit=Decimal('8.0'),
        signal_count=20
    )


class TestScoringIntegrationService:
    """Test ScoringIntegrationService functionality."""
    
    async def test_update_all_source_scores_success(self, integration_service, mock_uow):
        """Test successful batch update of all source scores."""
        
        # Mock active sources
        mock_sources = [Mock(id=1), Mock(id=2), Mock(id=3)]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        # Mock successful score updates
        with patch.object(integration_service, 'update_single_source_score') as mock_update:
            mock_update.return_value = Mock(spec=ScoreBreakdown)
            
            result = await integration_service.update_all_source_scores(batch_size=2)
            
            assert result['total_sources'] == 3
            assert result['successful_updates'] == 3
            assert result['failed_updates'] == 0
            assert result['skipped_sources'] == 0
            
    async def test_update_all_source_scores_no_sources(self, integration_service, mock_uow):
        """Test batch update when no sources exist."""
        
        mock_uow.signal_sources.active.return_value = []
        
        result = await integration_service.update_all_source_scores()
        
        assert result['total_sources'] == 0
        assert result['successful_updates'] == 0
        
    async def test_update_all_source_scores_partial_failure(self, integration_service, mock_uow):
        """Test batch update with some failures."""
        
        mock_sources = [Mock(id=1), Mock(id=2), Mock(id=3)]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        with patch.object(integration_service, 'update_single_source_score') as mock_update:
            # First call succeeds, second fails, third succeeds
            mock_update.side_effect = [
                Mock(spec=ScoreBreakdown),
                ScoringValidationError("Update failed"),
                Mock(spec=ScoreBreakdown)
            ]
            
            result = await integration_service.update_all_source_scores()
            
            assert result['total_sources'] == 3
            assert result['successful_updates'] == 2
            assert result['failed_updates'] == 1
            
    async def test_update_single_source_score_success(self, integration_service, mock_uow, sample_score_breakdown):
        """Test successful single source score update."""
        
        mock_uow.signal_sources.update_score.return_value = True
        
        with patch.object(integration_service._scoring_service, 'calculate_source_score') as mock_calc:
            mock_calc.return_value = sample_score_breakdown
            
            result = await integration_service.update_single_source_score(1)
            
            assert result == sample_score_breakdown
            mock_uow.signal_sources.update_score.assert_called_once_with(1, 750)
            mock_uow.commit.assert_called_once()
            
    async def test_update_single_source_score_database_failure(self, integration_service, mock_uow, sample_score_breakdown):
        """Test single source update when database update fails."""
        
        mock_uow.signal_sources.update_score.return_value = False
        
        with patch.object(integration_service._scoring_service, 'calculate_source_score') as mock_calc:
            mock_calc.return_value = sample_score_breakdown
            
            with pytest.raises(ScoringValidationError, match="Failed to update score"):
                await integration_service.update_single_source_score(1)
                
    async def test_bulk_score_update_success(self, integration_service, mock_uow):
        """Test successful bulk score update."""
        
        score_updates = {1: 750, 2: 680, 3: 920}
        mock_uow.signal_sources.batch_update_scores.return_value = 3
        
        result = await integration_service.bulk_score_update(score_updates)
        
        assert result == 3
        mock_uow.signal_sources.batch_update_scores.assert_called_once_with(score_updates)
        mock_uow.commit.assert_called_once()
        
    async def test_bulk_score_update_validation_error(self, integration_service, mock_uow):
        """Test bulk update with invalid score values."""
        
        invalid_updates = {1: -100, 2: 1500}  # Invalid scores
        
        with pytest.raises(ScoringValidationError):
            await integration_service.bulk_score_update(invalid_updates)
            
    async def test_recalculate_scores_for_time_window(self, integration_service, mock_uow):
        """Test recalculation of scores for specific time window."""
        
        mock_sources = [
            Mock(id=1, name="Source 1"),
            Mock(id=2, name="Source 2")
        ]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        mock_breakdown1 = ScoreBreakdown(
            score=800, display_score=8.0, tp_hit_rate_score=0.8,
            profitability_score=0.7, average_profit_score=0.6,
            best_profit_score=0.9, stop_loss_score=0.8, confidence_score=0.85,
            tp_hit_rate=Decimal('0.8'), stop_loss_rate=Decimal('0.2'),
            total_profit=Decimal('25.0'), average_profit=Decimal('1.5'),
            best_profit=Decimal('8.0'), signal_count=20
        )
        
        mock_breakdown2 = ScoreBreakdown(
            score=650, display_score=6.5, tp_hit_rate_score=0.6,
            profitability_score=0.5, average_profit_score=0.4,
            best_profit_score=0.7, stop_loss_score=0.9, confidence_score=0.75,
            tp_hit_rate=Decimal('0.6'), stop_loss_rate=Decimal('0.1'),
            total_profit=Decimal('18.0'), average_profit=Decimal('1.2'),
            best_profit=Decimal('6.0'), signal_count=15
        )
        
        with patch.object(integration_service._scoring_service, 'calculate_source_score') as mock_calc:
            mock_calc.side_effect = [mock_breakdown1, mock_breakdown2]
            
            time_window = TimeWindow.last_7d()
            result = await integration_service.recalculate_scores_for_time_window(time_window)
            
            assert result['time_window'] == 'last-7d'
            assert result['statistics']['total_sources'] == 2
            assert result['statistics']['sources_with_data'] == 2
            assert result['statistics']['average_score'] == 725.0  # (800 + 650) / 2
            assert result['statistics']['highest_score'] == 800
            assert result['statistics']['lowest_score'] == 650
            
            assert 1 in result['scores']
            assert 2 in result['scores']
            
    async def test_get_score_update_recommendations(self, integration_service, mock_uow):
        """Test score update recommendations based on activity."""
        
        mock_sources = [Mock(id=1), Mock(id=2), Mock(id=3), Mock(id=4)]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        # Mock statistics for different activity levels
        with patch.object(integration_service._statistics_service, 'get_source_statistics') as mock_get_stats:
            from app.core.dto import SignalStatistics
            
            # Source 1: High activity (3+ recent completions)
            # Source 2: Medium activity (1-2 recent completions)  
            # Source 3: Low activity (active signals but no recent completions)
            # Source 4: No activity (no signals at all)
            
            mock_get_stats.side_effect = [
                # Source 1: Recent stats, then all-time stats
                Mock(completed_signals=5, active_signals=2),  # High priority - recent
                Mock(total_signals=50),  # High priority - all-time
                # Source 2: Recent stats, then all-time stats
                Mock(completed_signals=2, active_signals=1),  # Medium priority - recent
                Mock(total_signals=20),  # Medium priority - all-time
                # Source 3: Recent stats, then all-time stats
                Mock(completed_signals=0, active_signals=3),  # Low priority - recent
                Mock(total_signals=10),  # Low priority - all-time
                # Source 4: Recent stats, then all-time stats
                Mock(completed_signals=0, active_signals=0),  # Skip candidate - recent
                Mock(total_signals=0),  # Skip - all-time
            ]
            
            result = await integration_service.get_score_update_recommendations()
            
            assert 1 in result['high_priority']
            assert 2 in result['medium_priority']
            assert 3 in result['low_priority']
            assert 4 in result['skip']
            
    async def test_validate_score_consistency_all_consistent(self, integration_service, mock_uow):
        """Test score consistency validation when all scores are consistent."""
        
        mock_sources = [
            Mock(id=1, name="Source 1", score=750),
            Mock(id=2, name="Source 2", score=680)
        ]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        with patch.object(integration_service._scoring_service, 'calculate_source_score') as mock_calc:
            # Return scores that match stored scores (within tolerance)
            mock_calc.side_effect = [
                Mock(score=752),  # Close enough (diff = 2)
                Mock(score=682)   # Close enough (diff = 2)
            ]
            
            result = await integration_service.validate_score_consistency()
            
            assert result['total_sources'] == 2
            assert result['consistent_sources'] == 2
            assert result['inconsistent_sources'] == 0
            assert len(result['inconsistencies']) == 0
            
    async def test_validate_score_consistency_with_inconsistencies(self, integration_service, mock_uow):
        """Test score consistency validation with significant differences."""
        
        mock_sources = [
            Mock(id=1, name="Source 1", score=750),
            Mock(id=2, name="Source 2", score=680)
        ]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        with patch.object(integration_service._scoring_service, 'calculate_source_score') as mock_calc:
            # Return scores that differ significantly from stored scores
            mock_calc.side_effect = [
                Mock(score=650, signal_count=20),  # Diff = 100 (significant)
                Mock(score=880, signal_count=15)   # Diff = 200 (significant)
            ]
            
            result = await integration_service.validate_score_consistency()
            
            assert result['total_sources'] == 2
            assert result['consistent_sources'] == 0
            assert result['inconsistent_sources'] == 2
            assert len(result['inconsistencies']) == 2
            
            # Check inconsistency details
            inconsistency1 = result['inconsistencies'][0]
            assert inconsistency1['source_id'] == 1
            assert inconsistency1['stored_score'] == 750
            assert inconsistency1['calculated_score'] == 650
            assert inconsistency1['difference'] == 100
            
    async def test_emergency_score_reset_success(self, integration_service, mock_uow):
        """Test successful emergency score reset."""
        
        mock_uow.signal_sources.update_score = AsyncMock(return_value=True)
        mock_uow.signal_sources.update_statistics = AsyncMock(return_value=True)
        
        result = await integration_service.emergency_score_reset(1, "Test reset")
        
        assert result is True
        mock_uow.signal_sources.update_score.assert_called_once_with(1, 0)
        mock_uow.commit.assert_called_once()
        
    async def test_emergency_score_reset_failure(self, integration_service, mock_uow):
        """Test emergency score reset failure."""
        
        mock_uow.signal_sources.update_score.return_value = False
        
        result = await integration_service.emergency_score_reset(1)
        
        assert result is False
        
    async def test_concurrent_batch_processing(self, integration_service, mock_uow):
        """Test concurrent processing of source batches."""
        
        # Create many sources to trigger batch processing
        mock_sources = [Mock(id=i) for i in range(1, 26)]  # 25 sources
        mock_uow.signal_sources.active.return_value = mock_sources
        
        with patch.object(integration_service, 'update_single_source_score') as mock_update:
            mock_update.return_value = Mock(spec=ScoreBreakdown)
            
            result = await integration_service.update_all_source_scores(batch_size=10)
            
            # Should process all 25 sources successfully
            assert result['total_sources'] == 25
            assert result['successful_updates'] == 25
            assert mock_update.call_count == 25
            
    async def test_error_handling_in_batch_processing(self, integration_service, mock_uow):
        """Test error handling during batch processing."""
        
        mock_sources = [Mock(id=1), Mock(id=2)]
        mock_uow.signal_sources.active.return_value = mock_sources
        
        with patch.object(integration_service, '_update_single_source_safe') as mock_safe_update:
            # Simulate one source updating successfully, one failing
            async def mock_update_effect(source_id, time_window, results):
                if source_id == 1:
                    results['successful_updates'] += 1
                else:
                    results['failed_updates'] += 1
                    
            mock_safe_update.side_effect = mock_update_effect
            
            result = await integration_service.update_all_source_scores(batch_size=1)
            
            assert result['successful_updates'] == 1
            assert result['failed_updates'] == 1