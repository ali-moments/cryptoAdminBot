"""
Unit tests for ScoringValidator.

Tests validation logic, edge case handling, and data sanitization.
"""

import pytest
from decimal import Decimal

from app.core.dto import SignalStatistics, ScoreBreakdown, TimeWindow
from app.services.validation import ScoringValidator, ScoringValidationError


@pytest.fixture
def validator():
    """Create ScoringValidator instance."""
    return ScoringValidator()


@pytest.fixture
def valid_statistics():
    """Create valid statistics for testing."""
    return SignalStatistics(
        source_id=1,
        total_signals=20,
        completed_signals=18,
        active_signals=2,
        tp_hit_count=12,
        stop_loss_count=6,
        cancelled_count=0,
        expired_count=0,
        tp_hit_rate=Decimal('0.6667'),
        stop_loss_rate=Decimal('0.3333'),
        total_profit=Decimal('25.50'),
        average_profit=Decimal('1.42'),
        best_profit=Decimal('8.75'),
        worst_profit=Decimal('-3.20'),
        profitable_signal_count=12,
        losing_signal_count=6
    )


class TestScoringValidator:
    """Test ScoringValidator functionality."""
    
    def test_validate_source_statistics_valid_data(self, validator, valid_statistics):
        """Test validation of valid statistics."""
        
        result = validator.validate_source_statistics(valid_statistics)
        
        assert isinstance(result, SignalStatistics)
        assert result.source_id == valid_statistics.source_id
        assert result.total_signals == valid_statistics.total_signals
        assert result.tp_hit_rate == valid_statistics.tp_hit_rate
        
    def test_validate_source_statistics_negative_counts(self, validator):
        """Test sanitization of negative counts."""
        
        stats = SignalStatistics(
            source_id=1,
            total_signals=-5,  # Invalid negative
            completed_signals=-3,  # Invalid negative
            active_signals=2,
            tp_hit_count=-1,  # Invalid negative
            stop_loss_count=0,
            cancelled_count=0,
            expired_count=0,
            tp_hit_rate=Decimal('0.5'),
            stop_loss_rate=Decimal('0.5'),
            total_profit=Decimal('10.0'),
            average_profit=Decimal('1.0'),
            best_profit=Decimal('5.0'),
            worst_profit=Decimal('-2.0'),
            profitable_signal_count=2,
            losing_signal_count=1
        )
        
        result = validator.validate_source_statistics(stats)
        
        # Negative counts should be sanitized to 0
        assert result.total_signals == 0
        assert result.completed_signals == 0
        assert result.tp_hit_count == 0
        
    def test_validate_source_statistics_invalid_rates(self, validator):
        """Test sanitization of rates outside 0-1 range."""
        
        stats = SignalStatistics(
            source_id=1,
            total_signals=10,
            completed_signals=10,
            active_signals=0,
            tp_hit_count=5,
            stop_loss_count=5,
            cancelled_count=0,
            expired_count=0,
            tp_hit_rate=Decimal('1.5'),  # Invalid > 1
            stop_loss_rate=Decimal('-0.2'),  # Invalid < 0
            total_profit=Decimal('10.0'),
            average_profit=Decimal('1.0'),
            best_profit=Decimal('5.0'),
            worst_profit=Decimal('-2.0'),
            profitable_signal_count=5,
            losing_signal_count=5
        )
        
        result = validator.validate_source_statistics(stats)
        
        # Rates should be clamped to 0-1
        assert result.tp_hit_rate == Decimal('1.0000')
        assert result.stop_loss_rate == Decimal('0.0000')
        
    def test_validate_source_statistics_extreme_profits(self, validator):
        """Test sanitization of extreme profit values."""
        
        stats = SignalStatistics(
            source_id=1,
            total_signals=5,
            completed_signals=5,
            active_signals=0,
            tp_hit_count=2,
            stop_loss_count=3,
            cancelled_count=0,
            expired_count=0,
            tp_hit_rate=Decimal('0.4'),
            stop_loss_rate=Decimal('0.6'),
            total_profit=Decimal('50000.0'),  # Extreme total profit
            average_profit=Decimal('15000.0'),  # Extreme average profit
            best_profit=Decimal('20000.0'),   # Extreme best profit
            worst_profit=Decimal('-200.0'),   # Extreme loss
            profitable_signal_count=2,
            losing_signal_count=3
        )
        
        result = validator.validate_source_statistics(stats)
        
        # Extreme values should be capped
        assert result.total_profit <= Decimal('1000000.0')  # 100x * 100 max signals
        assert result.average_profit <= Decimal('10000.0')  # Max profit cap
        assert result.best_profit <= Decimal('10000.0')
        assert result.worst_profit >= Decimal('-100.0')    # Min loss cap
        
    def test_validate_source_statistics_consistency_errors(self, validator):
        """Test detection of logical inconsistencies."""
        
        # Total signals < completed + active
        stats = SignalStatistics(
            source_id=1,
            total_signals=5,
            completed_signals=8,  # More than total
            active_signals=2,
            tp_hit_count=5,
            stop_loss_count=3,
            cancelled_count=0,
            expired_count=0,
            tp_hit_rate=Decimal('0.6'),
            stop_loss_rate=Decimal('0.4'),
            total_profit=Decimal('10.0'),
            average_profit=Decimal('1.0'),
            best_profit=Decimal('5.0'),
            worst_profit=Decimal('-2.0'),
            profitable_signal_count=5,
            losing_signal_count=3
        )
        
        with pytest.raises(ScoringValidationError, match="Inconsistent signal counts"):
            validator.validate_source_statistics(stats)
            
    def test_validate_score_breakdown_valid(self, validator):
        """Test validation of valid score breakdown."""
        
        breakdown = ScoreBreakdown(
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
        
        result = validator.validate_score_breakdown(breakdown)
        
        assert result.score == 750
        assert result.display_score == 7.50
        
    def test_validate_score_breakdown_out_of_range(self, validator):
        """Test validation clamps out-of-range values."""
        
        breakdown = ScoreBreakdown(
            score=1500,  # Over maximum
            display_score=15.0,  # Over maximum
            tp_hit_rate_score=1.5,  # Over maximum
            profitability_score=-0.2,  # Under minimum
            average_profit_score=0.6,
            best_profit_score=0.9,
            stop_loss_score=0.8,
            confidence_score=2.0,  # Over maximum
            tp_hit_rate=Decimal('0.8'),
            stop_loss_rate=Decimal('0.2'),
            total_profit=Decimal('25.0'),
            average_profit=Decimal('1.5'),
            best_profit=Decimal('8.0'),
            signal_count=20
        )
        
        result = validator.validate_score_breakdown(breakdown)
        
        # Should be clamped to valid ranges
        assert result.score == 1000  # Clamped to max
        assert result.display_score == 10.0  # Clamped to max
        assert result.tp_hit_rate_score == 1.0  # Clamped to max
        assert result.profitability_score == 0.0  # Clamped to min
        assert result.confidence_score == 1.0  # Clamped to max
        
    def test_handle_new_source_scoring_zero_signals(self, validator):
        """Test handling of source with zero signals."""
        
        result = validator.handle_new_source_scoring(1, 0)
        
        assert result.score == 0
        assert result.display_score == 0.0
        assert all(score == 0.0 for score in [
            result.tp_hit_rate_score,
            result.profitability_score,
            result.average_profit_score,
            result.best_profit_score,
            result.stop_loss_score,
            result.confidence_score
        ])
        
    def test_handle_new_source_scoring_few_signals(self, validator):
        """Test conservative scoring for sources with few signals."""
        
        result = validator.handle_new_source_scoring(1, 3)
        
        # Should get conservative score
        assert result.score <= 150  # Very conservative
        assert result.display_score <= 1.5
        assert result.confidence_score == 0.1  # Very low confidence
        
    def test_handle_new_source_scoring_moderate_signals(self, validator):
        """Test that moderate signal counts allow normal scoring."""
        
        result = validator.handle_new_source_scoring(1, 15)
        
        # Should return None to allow normal scoring
        assert result is None
        
    def test_handle_division_by_zero_cases(self, validator):
        """Test safe division by zero handling."""
        
        # Zero denominator
        result = validator.handle_division_by_zero_cases(Decimal('5'), 0)
        assert result == Decimal('0.0000')
        
        # Normal division
        result = validator.handle_division_by_zero_cases(Decimal('6'), 10)
        assert result == Decimal('0.6')
        
    def test_handle_empty_profit_population(self, validator):
        """Test handling of empty profit populations."""
        
        # Empty population
        result = validator.handle_empty_profit_population(Decimal('5'), [])
        assert result == 0.0
        
        # Single item population - equal value
        result = validator.handle_empty_profit_population(Decimal('5'), [Decimal('5')])
        assert result == 1.0
        
        # Single item population - lower value
        result = validator.handle_empty_profit_population(Decimal('3'), [Decimal('5')])
        assert result == 0.0
        
        # Very small population (conservative score)
        result = validator.handle_empty_profit_population(Decimal('5'), [Decimal('3'), Decimal('7')])
        assert result == 0.5
        
        # Adequate population size
        result = validator.handle_empty_profit_population(Decimal('5'), [Decimal(str(i)) for i in range(10)])
        assert result is None  # Should proceed with normal calculation
        
    def test_validate_time_window(self, validator):
        """Test time window validation."""
        
        # Valid time windows
        assert validator.validate_time_window(None) is None
        assert validator.validate_time_window(TimeWindow.all_time()) == TimeWindow.all_time()
        assert validator.validate_time_window(TimeWindow.last_48h()) == TimeWindow.last_48h()
        
        # Invalid time windows
        with pytest.raises(ScoringValidationError, match="Invalid time window"):
            validator.validate_time_window(TimeWindow("invalid", -5))
            
        with pytest.raises(ScoringValidationError, match="Invalid time window"):
            validator.validate_time_window(TimeWindow("invalid", 0))
            
        with pytest.raises(ScoringValidationError, match="Time window too large"):
            validator.validate_time_window(TimeWindow("huge", 400000))  # > 1 year
            
    def test_validate_source_id(self, validator):
        """Test source ID validation."""
        
        # Valid source IDs
        assert validator.validate_source_id(1) == 1
        assert validator.validate_source_id(999) == 999
        
        # Invalid source IDs
        with pytest.raises(ScoringValidationError, match="Invalid source ID"):
            validator.validate_source_id(0)
            
        with pytest.raises(ScoringValidationError, match="Invalid source ID"):
            validator.validate_source_id(-1)
            
        with pytest.raises(ScoringValidationError, match="Invalid source ID"):
            validator.validate_source_id("not_int")
            
    def test_validate_score_value(self, validator):
        """Test score value validation."""
        
        # Valid scores
        assert validator.validate_score_value(0) == 0
        assert validator.validate_score_value(500) == 500
        assert validator.validate_score_value(1000) == 1000
        
        # Invalid scores
        with pytest.raises(ScoringValidationError, match="Score .* outside valid range"):
            validator.validate_score_value(-1)
            
        with pytest.raises(ScoringValidationError, match="Score .* outside valid range"):
            validator.validate_score_value(1001)
            
        with pytest.raises(ScoringValidationError, match="Score must be integer"):
            validator.validate_score_value(5.5)
            
    def test_check_data_quality_warnings(self, validator):
        """Test data quality warning detection."""
        
        # Statistics that should trigger warnings
        problematic_stats = SignalStatistics(
            source_id=1,
            total_signals=5,  # Low sample size
            completed_signals=5,
            active_signals=0,
            tp_hit_count=5,  # 100% TP rate (suspicious)
            stop_loss_count=0,
            cancelled_count=0,
            expired_count=0,
            tp_hit_rate=Decimal('1.0000'),  # 100%
            stop_loss_rate=Decimal('0.0000'),
            total_profit=Decimal('25.0'),
            average_profit=Decimal('5.0'),
            best_profit=Decimal('1500.0'),  # Extreme profit
            worst_profit=Decimal('-150.0'),  # Extreme loss
            profitable_signal_count=5,
            losing_signal_count=0
        )
        
        warnings = validator.check_data_quality_warnings(problematic_stats)
        
        assert len(warnings) >= 3  # Should have multiple warnings
        assert any("Low sample size" in w for w in warnings)
        assert any("high TP rate" in w for w in warnings)
        assert any("Extreme best profit" in w for w in warnings)
        
    def test_check_data_quality_warnings_clean_data(self, validator, valid_statistics):
        """Test that clean data produces no warnings."""
        
        warnings = validator.check_data_quality_warnings(valid_statistics)
        
        # Should have no warnings for clean data
        assert len(warnings) == 0
        
    def test_sanitize_methods_integration(self, validator):
        """Test that all sanitization methods work together."""
        
        # Create statistics with multiple issues
        problematic_stats = SignalStatistics(
            source_id=1,
            total_signals=-5,  # Negative count
            completed_signals=10,  # Inconsistent with total
            active_signals=-2,  # Negative count
            tp_hit_count=15,  # More than completed
            stop_loss_count=-1,  # Negative count
            cancelled_count=0,
            expired_count=0,
            tp_hit_rate=Decimal('2.0'),  # Over 1.0
            stop_loss_rate=Decimal('-0.5'),  # Under 0.0
            total_profit=Decimal('50000.0'),  # Extreme value
            average_profit=Decimal('25000.0'),  # Extreme value
            best_profit=Decimal('30000.0'),  # Extreme value
            worst_profit=Decimal('-500.0'),  # Extreme loss
            profitable_signal_count=-3,  # Negative count
            losing_signal_count=-2  # Negative count
        )
        
        # Should raise error for inconsistent counts after sanitization
        with pytest.raises(ScoringValidationError):
            validator.validate_source_statistics(problematic_stats)