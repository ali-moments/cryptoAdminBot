"""
Unit tests for AnalyticsRanking.

Tests ranking functionality and leaderboards.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from decimal import Decimal

from app.analytics.ranking import AnalyticsRanking, RankingCriteria, RankedSource
from app.core.dto import ScoreBreakdown


@pytest.fixture
def mock_uow():
    """Create a mock UnitOfWork."""
    uow = Mock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    return uow


@pytest.fixture
def ranking_service(mock_uow):
    """Create AnalyticsRanking with mocked dependencies."""
    return AnalyticsRanking(mock_uow)


@pytest.fixture
def sample_score_breakdowns():
    """Create sample score breakdowns for testing."""
    return {
        1: ScoreBreakdown(
            score=850, display_score=8.5, tp_hit_rate_score=0.9,
            profitability_score=0.8, average_profit_score=0.7,
            best_profit_score=0.8, stop_loss_score=0.9, confidence_score=1.0,
            tp_hit_rate=Decimal('0.9'), stop_loss_rate=Decimal('0.1'),
            total_profit=Decimal('45.0'), average_profit=Decimal('2.25'),
            best_profit=Decimal('12.0'), signal_count=25
        ),
        2: ScoreBreakdown(
            score=720, display_score=7.2, tp_hit_rate_score=0.7,
            profitability_score=0.6, average_profit_score=0.8,
            best_profit_score=0.9, stop_loss_score=0.8, confidence_score=0.9,
            tp_hit_rate=Decimal('0.7'), stop_loss_rate=Decimal('0.2'),
            total_profit=Decimal('32.0'), average_profit=Decimal('2.0'),
            best_profit=Decimal('15.0'), signal_count=20
        ),
        3: ScoreBreakdown(
            score=650, display_score=6.5, tp_hit_rate_score=0.6,
            profitability_score=0.5, average_profit_score=0.6,
            best_profit_score=0.7, stop_loss_score=0.7, confidence_score=0.8,
            tp_hit_rate=Decimal('0.6'), stop_loss_rate=Decimal('0.3'),
            total_profit=Decimal('18.0'), average_profit=Decimal('1.2'),
            best_profit=Decimal('8.0'), signal_count=15
        )
    }


class TestAnalyticsRanking:
    """Test AnalyticsRanking functionality."""
    
    async def test_get_score_leaderboard_default_ranking(self, ranking_service, sample_score_breakdowns):
        """Test getting score leaderboard with default ranking."""
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.return_value = sample_score_breakdowns
            
            with patch.object(ranking_service, '_get_source_names') as mock_names:
                mock_names.return_value = {1: "Source Alpha", 2: "Source Beta", 3: "Source Gamma"}
                
                result = await ranking_service.get_score_leaderboard()
                
                assert len(result) == 3
                
                # Should be sorted by score (descending)
                assert result[0].source_id == 1  # Highest score (850)
                assert result[0].rank == 1
                assert result[0].source_name == "Source Alpha"
                
                assert result[1].source_id == 2  # Second highest (720)
                assert result[1].rank == 2
                
                assert result[2].source_id == 3  # Lowest (650)
                assert result[2].rank == 3
                
    async def test_get_score_leaderboard_with_limit(self, ranking_service, sample_score_breakdowns):
        """Test leaderboard with result limit."""
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.return_value = sample_score_breakdowns
            
            with patch.object(ranking_service, '_get_source_names') as mock_names:
                mock_names.return_value = {1: "Source A", 2: "Source B", 3: "Source C"}
                
                result = await ranking_service.get_score_leaderboard(limit=2)
                
                assert len(result) == 2
                assert result[0].source_id == 1
                assert result[1].source_id == 2
                
    async def test_get_score_leaderboard_with_min_signals(self, ranking_service, sample_score_breakdowns):
        """Test leaderboard filtering by minimum signal count."""
        
        criteria = RankingCriteria(metric="score", min_signals=20)
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.return_value = sample_score_breakdowns
            
            with patch.object(ranking_service, '_get_source_names') as mock_names:
                mock_names.return_value = {1: "Source A", 2: "Source B"}
                
                result = await ranking_service.get_score_leaderboard(criteria)
                
                # Should exclude source 3 (only 15 signals)
                assert len(result) == 2
                source_ids = [r.source_id for r in result]
                assert 1 in source_ids
                assert 2 in source_ids
                assert 3 not in source_ids
                
    async def test_get_score_leaderboard_different_metrics(self, ranking_service, sample_score_breakdowns):
        """Test ranking by different metrics."""
        
        # Test ranking by TP rate
        criteria = RankingCriteria(metric="tp_rate")
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.return_value = sample_score_breakdowns
            
            with patch.object(ranking_service, '_get_source_names') as mock_names:
                mock_names.return_value = {1: "A", 2: "B", 3: "C"}
                
                result = await ranking_service.get_score_leaderboard(criteria)
                
                # Should be sorted by TP rate: 1 (0.9), 2 (0.7), 3 (0.6)
                assert result[0].source_id == 1
                assert result[1].source_id == 2
                assert result[2].source_id == 3
                
    async def test_get_performance_tiers(self, ranking_service, sample_score_breakdowns):
        """Test performance tier classification."""
        
        # Add sources in different tiers
        extended_breakdowns = sample_score_breakdowns.copy()
        extended_breakdowns[4] = ScoreBreakdown(
            score=920, display_score=9.2, tp_hit_rate_score=0.9,
            profitability_score=0.9, average_profit_score=0.9,
            best_profit_score=0.9, stop_loss_score=0.9, confidence_score=1.0,
            tp_hit_rate=Decimal('0.9'), stop_loss_rate=Decimal('0.1'),
            total_profit=Decimal('60.0'), average_profit=Decimal('3.0'),
            best_profit=Decimal('20.0'), signal_count=30
        )
        extended_breakdowns[5] = ScoreBreakdown(
            score=450, display_score=4.5, tp_hit_rate_score=0.4,
            profitability_score=0.3, average_profit_score=0.5,
            best_profit_score=0.6, stop_loss_score=0.6, confidence_score=0.7,
            tp_hit_rate=Decimal('0.4'), stop_loss_rate=Decimal('0.4'),
            total_profit=Decimal('8.0'), average_profit=Decimal('0.8'),
            best_profit=Decimal('5.0'), signal_count=10
        )
        
        with patch.object(ranking_service, 'get_score_leaderboard') as mock_leaderboard:
            mock_leaderboard.return_value = [
                RankedSource(4, 1, extended_breakdowns[4], "Elite Source"),
                RankedSource(1, 2, extended_breakdowns[1], "Excellent Source"),
                RankedSource(2, 3, extended_breakdowns[2], "Good Source"),
                RankedSource(3, 4, extended_breakdowns[3], "Average Source"),
                RankedSource(5, 5, extended_breakdowns[5], "Poor Source"),
            ]
            
            result = await ranking_service.get_performance_tiers()
            
            assert len(result["elite"]) == 1      # Score 920 (9.2/10)
            assert len(result["excellent"]) == 1  # Score 850 (8.5/10)
            assert len(result["good"]) == 1       # Score 720 (7.2/10)
            assert len(result["average"]) == 1    # Score 650 (6.5/10)
            assert len(result["below_average"]) == 0
            assert len(result["poor"]) == 1       # Score 450 (4.5/10)
            
    async def test_get_rising_stars(self, ranking_service):
        """Test rising stars detection."""
        
        # Mock recent vs all-time scores
        recent_scores = {
            1: ScoreBreakdown(score=800, signal_count=10, **{k: 0.8 for k in ['display_score', 'tp_hit_rate_score', 'profitability_score', 'average_profit_score', 'best_profit_score', 'stop_loss_score', 'confidence_score']}, **{k: Decimal('0.8') for k in ['tp_hit_rate', 'stop_loss_rate', 'total_profit', 'average_profit', 'best_profit']}),
            2: ScoreBreakdown(score=650, signal_count=8, **{k: 0.65 for k in ['display_score', 'tp_hit_rate_score', 'profitability_score', 'average_profit_score', 'best_profit_score', 'stop_loss_score', 'confidence_score']}, **{k: Decimal('0.65') for k in ['tp_hit_rate', 'stop_loss_rate', 'total_profit', 'average_profit', 'best_profit']})
        }
        
        all_time_scores = {
            1: ScoreBreakdown(score=650, signal_count=25, **{k: 0.65 for k in ['display_score', 'tp_hit_rate_score', 'profitability_score', 'average_profit_score', 'best_profit_score', 'stop_loss_score', 'confidence_score']}, **{k: Decimal('0.65') for k in ['tp_hit_rate', 'stop_loss_rate', 'total_profit', 'average_profit', 'best_profit']}),
            2: ScoreBreakdown(score=680, signal_count=20, **{k: 0.68 for k in ['display_score', 'tp_hit_rate_score', 'profitability_score', 'average_profit_score', 'best_profit_score', 'stop_loss_score', 'confidence_score']}, **{k: Decimal('0.68') for k in ['tp_hit_rate', 'stop_loss_rate', 'total_profit', 'average_profit', 'best_profit']})
        }
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.side_effect = [recent_scores, all_time_scores]
            
            result = await ranking_service.get_rising_stars(min_recent_signals=5, limit=10)
            
            # Source 1 improved from 650 to 800 (+150)
            # Source 2 declined from 680 to 650 (-30)
            
            assert len(result) == 1  # Only source 1 qualifies as rising star
            assert result[0]['source_id'] == 1
            assert result[0]['improvement'] == 150
            assert result[0]['recent_score'] == 800
            assert result[0]['all_time_score'] == 650
            
    async def test_get_consistency_ranking(self, ranking_service):
        """Test consistency ranking calculation."""
        
        # Mock statistics for sources with different consistency profiles
        with patch.object(ranking_service._statistics_service, 'get_all_sources_statistics') as mock_stats:
            from app.core.dto import SignalStatistics
            
            mock_stats.return_value = {
                1: SignalStatistics(
                    source_id=1, total_signals=50, completed_signals=45, active_signals=5,
                    tp_hit_count=35, stop_loss_count=10, cancelled_count=0, expired_count=0,
                    tp_hit_rate=Decimal('0.78'), stop_loss_rate=Decimal('0.22'),
                    total_profit=Decimal('85.0'), average_profit=Decimal('1.89'),
                    best_profit=Decimal('8.5'), worst_profit=Decimal('-2.1'),
                    profitable_signal_count=35, losing_signal_count=10
                ),
                2: SignalStatistics(
                    source_id=2, total_signals=25, completed_signals=20, active_signals=5,
                    tp_hit_count=15, stop_loss_count=5, cancelled_count=0, expired_count=0,
                    tp_hit_rate=Decimal('0.75'), stop_loss_rate=Decimal('0.25'),
                    total_profit=Decimal('40.0'), average_profit=Decimal('2.0'),
                    best_profit=Decimal('6.0'), worst_profit=Decimal('-1.5'),
                    profitable_signal_count=15, losing_signal_count=5
                )
            }
            
            result = await ranking_service.get_consistency_ranking()
            
            assert len(result) >= 1  # Should have at least one qualifying source
            for ranking in result:
                assert 'source_id' in ranking
                assert 'consistency_score' in ranking
                assert 'tp_consistency' in ranking
                assert 'profit_consistency' in ranking
                assert ranking['consistency_score'] >= 0.0
                assert ranking['consistency_score'] <= 1.0
                
    async def test_get_metric_leaders(self, ranking_service):
        """Test metric leaders identification."""
        
        with patch.object(ranking_service, 'get_score_leaderboard') as mock_leaderboard:
            # Mock different leaders for different metrics
            mock_leaderboard.side_effect = [
                [RankedSource(1, 1, Mock(score=900), "TP Leader")],      # tp_rate leader
                [RankedSource(2, 1, Mock(score=850), "Profit Leader")],   # profit leader
                [RankedSource(3, 1, Mock(score=800), "AvgProfit Leader")], # avg_profit leader
                [RankedSource(1, 1, Mock(score=900), "Signal Leader")],   # signal_count leader
                [RankedSource(2, 1, Mock(score=880), "Consistency Leader")] # consistency leader
            ]
            
            result = await ranking_service.get_metric_leaders()
            
            assert 'tp_rate' in result
            assert 'profit' in result
            assert 'avg_profit' in result
            assert 'signal_count' in result
            assert 'consistency' in result
            
            assert result['tp_rate'].source_id == 1
            assert result['profit'].source_id == 2
            assert result['avg_profit'].source_id == 3
            
    async def test_compare_sources(self, ranking_service, sample_score_breakdowns):
        """Test direct source comparison."""
        
        source_ids = [1, 3]  # Compare sources 1 and 3
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.return_value = sample_score_breakdowns
            
            with patch.object(ranking_service, '_get_source_names') as mock_names:
                mock_names.return_value = {1: "Alpha", 3: "Gamma"}
                
                result = await ranking_service.compare_sources(source_ids)
                
                assert len(result) == 2
                
                # Should be ranked by score (1 > 3)
                assert result[0].source_id == 1
                assert result[0].rank == 1
                assert result[0].source_name == "Alpha"
                
                assert result[1].source_id == 3
                assert result[1].rank == 2
                assert result[1].source_name == "Gamma"
                
    async def test_empty_leaderboard(self, ranking_service):
        """Test handling of empty leaderboard."""
        
        with patch.object(ranking_service._scoring_service, 'calculate_all_scores') as mock_scores:
            mock_scores.return_value = {}
            
            result = await ranking_service.get_score_leaderboard()
            
            assert len(result) == 0
            
    async def test_sort_by_metric_edge_cases(self, ranking_service):
        """Test sorting by metrics with edge cases."""
        
        scores = {
            1: Mock(score=800, tp_hit_rate=Decimal('0.8'), total_profit=Decimal('25.0'), 
                   average_profit=Decimal('2.0'), signal_count=20),
            2: Mock(score=800, tp_hit_rate=Decimal('0.8'), total_profit=Decimal('25.0'),
                   average_profit=Decimal('2.0'), signal_count=20)  # Identical values
        }
        
        criteria = RankingCriteria(metric="score", ascending=False)
        result = await ranking_service._sort_by_metric(scores, criteria)
        
        # Should handle identical values gracefully
        assert len(result) == 2
        assert all(isinstance(item, tuple) and len(item) == 2 for item in result)