"""
Unit tests for ScoringService.

Tests score calculations, component weighting, and percentile normalization.
"""

import pytest
from decimal import Decimal
from unittest.mock import Mock, AsyncMock, patch

from app.core.dto import SignalStatistics, ScoreBreakdown, TimeWindow
from app.services.scoring import ScoringService
from app.services.statistics import StatisticsService
from app.services.validation import ScoringValidationError


@pytest.fixture
def mock_statistics_service():
    """Create a mock StatisticsService."""
    return Mock(spec=StatisticsService)


@pytest.fixture  
def scoring_service(mock_statistics_service):
    """Create ScoringService with mocked dependencies."""
    return ScoringService(mock_statistics_service)


@pytest.fixture
def sample_statistics():
    """Create sample statistics for testing."""
    return SignalStatistics(
        source_id=1,
        total_signals=20,
        completed_signals=18,
        active_signals=2,
        tp_hit_count=12,
        stop_loss_count=6,
        cancelled_count=0,
        expired_count=0,
        tp_hit_rate=Decimal('0.6667'),  # 12/18
        stop_loss_rate=Decimal('0.3333'),  # 6/18
        total_profit=Decimal('25.50'),
        average_profit=Decimal('1.42'),
        best_profit=Decimal('8.75'),
        worst_profit=Decimal('-3.20'),
        profitable_signal_count=12,
        losing_signal_count=6
    )


class TestScoringService:
    """Test ScoringService functionality."""
    
    async def test_calculate_source_score_normal_case(self, scoring_service, mock_statistics_service, sample_statistics):
        """Test normal score calculation with good data."""
        
        # Mock dependencies
        mock_statistics_service.get_source_statistics.return_value = sample_statistics
        mock_statistics_service.get_profit_percentiles.return_value = {
            'total_profits': [Decimal('10.0'), Decimal('25.50'), Decimal('30.0')],
            'average_profits': [Decimal('0.5'), Decimal('1.42'), Decimal('2.0')],
            'best_profits': [Decimal('5.0'), Decimal('8.75'), Decimal('12.0')]
        }
        mock_statistics_service.calculate_confidence_score.return_value = 0.9  # sqrt(20/25) ≈ 0.89
        
        result = await scoring_service.calculate_source_score(1)
        
        assert isinstance(result, ScoreBreakdown)
        assert 0 <= result.score <= 1000
        assert 0.0 <= result.display_score <= 10.0
        assert result.score == int(result.display_score * 100)
        assert result.signal_count == 20
        assert result.tp_hit_rate == Decimal('0.6667')
        
        # Check component scores are in valid range
        assert 0.0 <= result.tp_hit_rate_score <= 1.0
        assert 0.0 <= result.profitability_score <= 1.0
        assert 0.0 <= result.confidence_score <= 1.0
        
    async def test_calculate_source_score_zero_signals(self, scoring_service, mock_statistics_service):
        """Test score calculation for source with zero signals."""
        
        zero_stats = SignalStatistics(
            source_id=1, total_signals=0, completed_signals=0, active_signals=0,
            tp_hit_count=0, stop_loss_count=0, cancelled_count=0, expired_count=0,
            tp_hit_rate=Decimal('0.0000'), stop_loss_rate=Decimal('0.0000'),
            total_profit=Decimal('0.0000'), average_profit=Decimal('0.0000'),
            best_profit=None, worst_profit=None,
            profitable_signal_count=0, losing_signal_count=0
        )
        
        mock_statistics_service.get_source_statistics.return_value = zero_stats
        
        result = await scoring_service.calculate_source_score(1)
        
        assert result.score == 0
        assert result.display_score == 0.0
        assert result.signal_count == 0
        assert all(score == 0.0 for score in [
            result.tp_hit_rate_score,
            result.profitability_score, 
            result.average_profit_score,
            result.best_profit_score,
            result.stop_loss_score,
            result.confidence_score
        ])
        
    async def test_calculate_source_score_new_source_low_signals(self, scoring_service, mock_statistics_service):
        """Test conservative scoring for new sources with few signals."""
        
        new_source_stats = SignalStatistics(
            source_id=1, total_signals=3, completed_signals=2, active_signals=1,
            tp_hit_count=2, stop_loss_count=0, cancelled_count=0, expired_count=0,
            tp_hit_rate=Decimal('1.0000'), stop_loss_rate=Decimal('0.0000'),
            total_profit=Decimal('10.0'), average_profit=Decimal('5.0'),
            best_profit=Decimal('7.0'), worst_profit=Decimal('3.0'),
            profitable_signal_count=2, losing_signal_count=0
        )
        
        mock_statistics_service.get_source_statistics.return_value = new_source_stats
        
        result = await scoring_service.calculate_source_score(1)
        
        # Should get conservative score despite perfect stats
        assert result.score <= 300  # Very conservative for 3 signals
        assert result.confidence_score == 0.1  # Low confidence
        
    async def test_calculate_source_score_perfect_performance(self, scoring_service, mock_statistics_service):
        """Test score calculation with perfect performance metrics."""
        
        perfect_stats = SignalStatistics(
            source_id=1, total_signals=100, completed_signals=100, active_signals=0,
            tp_hit_count=100, stop_loss_count=0, cancelled_count=0, expired_count=0,
            tp_hit_rate=Decimal('1.0000'), stop_loss_rate=Decimal('0.0000'),
            total_profit=Decimal('500.0'), average_profit=Decimal('5.0'),
            best_profit=Decimal('25.0'), worst_profit=Decimal('1.0'),
            profitable_signal_count=100, losing_signal_count=0
        )
        
        # Mock percentiles to make this source the best
        mock_statistics_service.get_source_statistics.return_value = perfect_stats
        mock_statistics_service.get_profit_percentiles.return_value = {
            'total_profits': [Decimal('100.0'), Decimal('200.0'), Decimal('500.0')],
            'average_profits': [Decimal('1.0'), Decimal('2.0'), Decimal('5.0')],
            'best_profits': [Decimal('10.0'), Decimal('15.0'), Decimal('25.0')]
        }
        mock_statistics_service.calculate_confidence_score.return_value = 1.0
        
        result = await scoring_service.calculate_source_score(1)
        
        # Should get high score for perfect performance
        assert result.score >= 900  # Near maximum
        assert result.tp_hit_rate_score == 1.0  # Perfect TP rate
        assert result.stop_loss_score == 1.0   # No stop losses
        assert result.confidence_score == 1.0  # Full confidence
        
    async def test_calculate_source_score_poor_performance(self, scoring_service, mock_statistics_service):
        """Test score calculation with poor performance metrics."""
        
        poor_stats = SignalStatistics(
            source_id=1, total_signals=50, completed_signals=50, active_signals=0,
            tp_hit_count=10, stop_loss_count=40, cancelled_count=0, expired_count=0,
            tp_hit_rate=Decimal('0.2000'), stop_loss_rate=Decimal('0.8000'),
            total_profit=Decimal('-75.0'), average_profit=Decimal('-1.5'),
            best_profit=Decimal('2.0'), worst_profit=Decimal('-15.0'),
            profitable_signal_count=10, losing_signal_count=40
        )
        
        mock_statistics_service.get_source_statistics.return_value = poor_stats
        mock_statistics_service.get_profit_percentiles.return_value = {
            'total_profits': [Decimal('-75.0'), Decimal('0.0'), Decimal('50.0')],
            'average_profits': [Decimal('-1.5'), Decimal('0.0'), Decimal('1.0')],
            'best_profits': [Decimal('2.0'), Decimal('5.0'), Decimal('10.0')]
        }
        mock_statistics_service.calculate_confidence_score.return_value = 0.7
        
        result = await scoring_service.calculate_source_score(1)
        
        # Should get low score for poor performance
        assert result.score <= 300  # Low score
        assert result.tp_hit_rate_score == 0.2  # Low TP rate
        assert abs(result.stop_loss_score - 0.2) < 1e-10    # High stop loss rate (with precision tolerance)
        
    async def test_percentile_score_calculation_edge_cases(self, scoring_service):
        """Test percentile score calculation with edge cases."""
        
        # Empty population
        result = scoring_service._calculate_percentile_score_safe(Decimal('5.0'), [])
        assert result == 0.0
        
        # Single item population
        result = scoring_service._calculate_percentile_score_safe(Decimal('5.0'), [Decimal('5.0')])
        assert result == 1.0
        
        result = scoring_service._calculate_percentile_score_safe(Decimal('3.0'), [Decimal('5.0')])
        assert result == 0.0
        
        # Small population (should return conservative score)
        result = scoring_service._calculate_percentile_score_safe(Decimal('5.0'), [Decimal('3.0'), Decimal('7.0')])
        assert result == 0.5  # Conservative score for small population
        
    async def test_percentile_rank_calculation(self, scoring_service):
        """Test percentile rank calculation."""
        
        population = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
        
        # Test various values
        assert scoring_service._percentile_rank(1.0, population) == 5.0   # Bottom
        assert scoring_service._percentile_rank(10.0, population) == 95.0  # Top
        assert scoring_service._percentile_rank(5.5, population) == 50.0   # Middle
        
        # Test edge cases
        assert scoring_service._percentile_rank(0.0, population) == 0.0    # Below all
        assert scoring_service._percentile_rank(11.0, population) == 100.0 # Above all
        
    async def test_calculate_all_scores(self, scoring_service, mock_statistics_service):
        """Test calculating scores for all sources."""
        
        # Mock statistics for multiple sources
        stats_dict = {
            1: Mock(spec=SignalStatistics),
            2: Mock(spec=SignalStatistics),
            3: Mock(spec=SignalStatistics)
        }
        
        mock_statistics_service.get_all_sources_statistics.return_value = stats_dict
        
        # Mock individual score calculations
        with patch.object(scoring_service, 'calculate_source_score') as mock_calc:
            mock_calc.return_value = Mock(spec=ScoreBreakdown)
            
            result = await scoring_service.calculate_all_scores()
            
            assert len(result) == 3
            assert all(source_id in result for source_id in [1, 2, 3])
            assert mock_calc.call_count == 3
            
    async def test_convert_score_formats(self, scoring_service):
        """Test score format conversion methods."""
        
        # Test internal to display conversion
        assert scoring_service.convert_to_display_score(0) == 0.00
        assert scoring_service.convert_to_display_score(500) == 5.00
        assert scoring_service.convert_to_display_score(891) == 8.91
        assert scoring_service.convert_to_display_score(1000) == 10.00
        
        # Test display to internal conversion
        assert scoring_service.convert_from_display_score(0.00) == 0
        assert scoring_service.convert_from_display_score(5.00) == 500
        assert scoring_service.convert_from_display_score(8.91) == 891
        assert scoring_service.convert_from_display_score(10.00) == 1000
        
        # Test clamping
        assert scoring_service.convert_from_display_score(-1.0) == 0
        assert scoring_service.convert_from_display_score(15.0) == 1000
        
    async def test_explain_score(self, scoring_service):
        """Test score explanation generation."""
        
        breakdown = ScoreBreakdown(
            score=675,
            display_score=6.75,
            tp_hit_rate_score=0.8,
            profitability_score=0.6,
            average_profit_score=0.7,
            best_profit_score=0.5,
            stop_loss_score=0.9,
            confidence_score=0.85,
            tp_hit_rate=Decimal('0.8000'),
            stop_loss_rate=Decimal('0.1000'),
            total_profit=Decimal('25.50'),
            average_profit=Decimal('1.42'),
            best_profit=Decimal('8.75'),
            signal_count=20
        )
        
        explanation = scoring_service.explain_score(breakdown)
        
        assert "6.75/10" in explanation
        assert "675/1000" in explanation
        assert "TP Hit Rate: 0.800 × 30%" in explanation
        assert "80.0%" in explanation  # TP hit rate percentage
        assert "10.0%" in explanation  # Stop loss rate percentage
        
    async def test_score_validation(self, scoring_service, mock_statistics_service):
        """Test score validation and edge case handling."""
        
        # Invalid source ID
        with pytest.raises(ScoringValidationError):
            await scoring_service.calculate_source_score(-1)
            
        # Database error handling
        mock_statistics_service.get_source_statistics.side_effect = Exception("DB Error")
        
        with pytest.raises(ScoringValidationError, match="Failed to calculate score"):
            await scoring_service.calculate_source_score(1)
            
    async def test_component_weights(self, scoring_service, mock_statistics_service, sample_statistics):
        """Test that component weights sum to 1.0 and are applied correctly."""
        
        # Mock to return consistent data
        mock_statistics_service.get_source_statistics.return_value = sample_statistics
        mock_statistics_service.get_profit_percentiles.return_value = {
            'total_profits': [Decimal('25.50')],
            'average_profits': [Decimal('1.42')],
            'best_profits': [Decimal('8.75')]
        }
        mock_statistics_service.calculate_confidence_score.return_value = 1.0
        
        result = await scoring_service.calculate_source_score(1)
        
        # Manually calculate expected score to verify weights
        expected_raw_score = (
            0.30 * float(sample_statistics.tp_hit_rate) +    # 30%
            0.25 * 1.0 +                                     # 25% (percentile score)
            0.15 * 1.0 +                                     # 15% (percentile score)
            0.10 * 1.0 +                                     # 10% (percentile score)
            0.10 * (1.0 - float(sample_statistics.stop_loss_rate)) + # 10%
            0.10 * 1.0                                       # 10%
        )
        
        expected_score = round(1000 * expected_raw_score)
        
        # Allow for small rounding differences
        assert abs(result.score - expected_score) <= 2
        
    async def test_time_window_parameter(self, scoring_service, mock_statistics_service, sample_statistics):
        """Test that time window parameter is passed correctly."""
        
        time_window = TimeWindow.last_7d()
        
        mock_statistics_service.get_source_statistics.return_value = sample_statistics
        mock_statistics_service.get_profit_percentiles.return_value = {
            'total_profits': [], 'average_profits': [], 'best_profits': []
        }
        mock_statistics_service.calculate_confidence_score.return_value = 0.5
        
        await scoring_service.calculate_source_score(1, time_window)
        
        # Verify time window was passed to dependencies
        mock_statistics_service.get_source_statistics.assert_called_with(1, time_window)
        mock_statistics_service.get_profit_percentiles.assert_called_with(time_window)