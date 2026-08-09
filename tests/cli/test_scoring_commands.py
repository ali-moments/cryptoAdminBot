"""
Unit tests for scoring CLI commands.

Tests CLI command functionality and integration.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from click.testing import CliRunner

from app.cli.scoring_commands import (
    update_all_scores, 
    update_source_score,
    validate_consistency,
    leaderboard
)


@pytest.fixture
def cli_runner():
    """Create Click CLI runner."""
    return CliRunner()


@pytest.fixture
def mock_app():
    """Create mock application."""
    app = Mock()
    app.uow = Mock()
    return app


class TestScoringCLICommands:
    """Test scoring CLI commands."""
    
    def test_update_all_scores_dry_run(self, cli_runner):
        """Test update-all-scores command in dry-run mode."""
        
        mock_result = {
            'time_window': 'all-time',
            'statistics': {
                'total_sources': 3,
                'sources_with_data': 2,
                'average_score': 750.0,
                'highest_score': 850,
                'lowest_score': 650
            },
            'scores': {
                1: {'score': 850, 'source_name': 'Source 1', 'signal_count': 25},
                2: {'score': 650, 'source_name': 'Source 2', 'signal_count': 15}
            }
        }
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.recalculate_scores_for_time_window = AsyncMock(return_value=mock_result)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(update_all_scores, ['--dry-run'])
                
                assert result.exit_code == 0
                assert "DRY RUN RESULTS" in result.output
                assert "Total sources: 3" in result.output
                assert "Sources with data: 2" in result.output
                
    def test_update_all_scores_actual_update(self, cli_runner):
        """Test update-all-scores command with actual update."""
        
        mock_result = {
            'total_sources': 3,
            'successful_updates': 3,
            'failed_updates': 0,
            'skipped_sources': 0
        }
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.update_all_source_scores = AsyncMock(return_value=mock_result)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(update_all_scores, ['--batch-size', '5'])
                
                assert result.exit_code == 0
                assert "UPDATE RESULTS" in result.output
                assert "Successful updates: 3" in result.output
                assert "All sources updated successfully" in result.output
                
    def test_update_all_scores_with_failures(self, cli_runner):
        """Test update-all-scores command with some failures."""
        
        mock_result = {
            'total_sources': 3,
            'successful_updates': 2,
            'failed_updates': 1,
            'skipped_sources': 0
        }
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.update_all_source_scores = AsyncMock(return_value=mock_result)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(update_all_scores)
                
                assert result.exit_code == 1  # Should exit with error code
                assert "1 sources failed to update" in result.output
                
    def test_update_source_score_success(self, cli_runner):
        """Test update-source-score command success."""
        
        from app.core.dto import ScoreBreakdown
        from decimal import Decimal
        
        mock_breakdown = ScoreBreakdown(
            score=750, display_score=7.50, tp_hit_rate_score=0.8,
            profitability_score=0.7, average_profit_score=0.6,
            best_profit_score=0.9, stop_loss_score=0.8, confidence_score=0.85,
            tp_hit_rate=Decimal('0.8'), stop_loss_rate=Decimal('0.2'),
            total_profit=Decimal('25.0'), average_profit=Decimal('1.5'),
            best_profit=Decimal('8.0'), signal_count=20
        )
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.update_single_source_score = AsyncMock(return_value=mock_breakdown)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(update_source_score, ['123'])
                
                assert result.exit_code == 0
                assert "SCORE UPDATE SUCCESSFUL" in result.output
                assert "Source ID: 123" in result.output
                assert "New Score: 750/1000 (7.50/10)" in result.output
                
    def test_update_source_score_with_explanation(self, cli_runner):
        """Test update-source-score command with detailed explanation."""
        
        from app.core.dto import ScoreBreakdown
        from decimal import Decimal
        
        mock_breakdown = ScoreBreakdown(
            score=750, display_score=7.50, tp_hit_rate_score=0.8,
            profitability_score=0.7, average_profit_score=0.6,
            best_profit_score=0.9, stop_loss_score=0.8, confidence_score=0.85,
            tp_hit_rate=Decimal('0.8'), stop_loss_rate=Decimal('0.2'),
            total_profit=Decimal('25.0'), average_profit=Decimal('1.5'),
            best_profit=Decimal('8.0'), signal_count=20
        )
        
        mock_explanation = "Score: 7.50/10 (750/1000)\n\nComponent Breakdown:\n  TP Hit Rate: 0.800 × 30% = 0.240"
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.update_single_source_score = AsyncMock(return_value=mock_breakdown)
            
            mock_scoring_service = Mock()
            mock_scoring_service.explain_score.return_value = mock_explanation
            
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                with patch('app.services.scoring.ScoringService', return_value=mock_scoring_service):
                    with patch('app.services.statistics.StatisticsService'):
                        result = cli_runner.invoke(update_source_score, ['123', '--explain'])
                        
                        assert result.exit_code == 0
                        assert "DETAILED BREAKDOWN" in result.output
                        assert "TP Hit Rate: 0.800" in result.output
                        
    def test_validate_consistency_all_good(self, cli_runner):
        """Test validate-consistency command with all scores consistent."""
        
        mock_report = {
            'total_sources': 5,
            'consistent_sources': 5,
            'inconsistent_sources': 0,
            'inconsistencies': []
        }
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.validate_score_consistency = AsyncMock(return_value=mock_report)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(validate_consistency)
                
                assert result.exit_code == 0
                assert "CONSISTENCY VALIDATION" in result.output
                assert "Total sources: 5" in result.output
                assert "All scores are consistent!" in result.output
                
    def test_validate_consistency_with_issues(self, cli_runner):
        """Test validate-consistency command with inconsistencies."""
        
        mock_report = {
            'total_sources': 3,
            'consistent_sources': 1,
            'inconsistent_sources': 2,
            'inconsistencies': [
                {
                    'source_id': 1,
                    'source_name': 'Source Alpha',
                    'stored_score': 750,
                    'calculated_score': 680,
                    'difference': 70
                },
                {
                    'source_id': 2,
                    'error': 'Database connection failed'
                }
            ]
        }
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.validate_score_consistency = AsyncMock(return_value=mock_report)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(validate_consistency)
                
                assert result.exit_code == 0
                assert "Found 2 inconsistencies:" in result.output
                assert "Source Alpha" in result.output
                assert "stored=750" in result.output
                assert "Database connection failed" in result.output
                
    def test_leaderboard_command(self, cli_runner):
        """Test leaderboard command."""
        
        from app.analytics.ranking import RankedSource
        from app.core.dto import ScoreBreakdown
        from decimal import Decimal
        
        mock_leaderboard = [
            RankedSource(
                source_id=1, rank=1,
                score_breakdown=ScoreBreakdown(
                    score=850, display_score=8.50, tp_hit_rate_score=0.9,
                    profitability_score=0.8, average_profit_score=0.7,
                    best_profit_score=0.8, stop_loss_score=0.9, confidence_score=1.0,
                    tp_hit_rate=Decimal('0.85'), stop_loss_rate=Decimal('0.15'),
                    total_profit=Decimal('45.0'), average_profit=Decimal('2.25'),
                    best_profit=Decimal('12.0'), signal_count=25
                ),
                source_name="Alpha Source"
            ),
            RankedSource(
                source_id=2, rank=2,
                score_breakdown=ScoreBreakdown(
                    score=720, display_score=7.20, tp_hit_rate_score=0.7,
                    profitability_score=0.6, average_profit_score=0.8,
                    best_profit_score=0.9, stop_loss_score=0.8, confidence_score=0.9,
                    tp_hit_rate=Decimal('0.70'), stop_loss_rate=Decimal('0.30'),
                    total_profit=Decimal('32.0'), average_profit=Decimal('2.0'),
                    best_profit=Decimal('15.0'), signal_count=20
                ),
                source_name="Beta Source"
            )
        ]
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_ranking = Mock()
            mock_ranking.get_score_leaderboard = AsyncMock(return_value=mock_leaderboard)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.AnalyticsRanking', return_value=mock_ranking):
                result = cli_runner.invoke(leaderboard, ['--limit', '5'])
                
                assert result.exit_code == 0
                assert "TOP 5 SIGNAL SOURCES" in result.output
                assert "Alpha Source" in result.output
                assert "Beta Source" in result.output
                assert "8.50/10" in result.output
                assert "7.20/10" in result.output
                
    def test_command_error_handling(self, cli_runner):
        """Test CLI command error handling."""
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.update_single_source_score = AsyncMock(
                side_effect=Exception("Database error")
            )
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                result = cli_runner.invoke(update_source_score, ['123'])
                
                assert result.exit_code == 1
                assert "Failed to update source 123" in result.output
                
    def test_time_window_parameter_handling(self, cli_runner):
        """Test time window parameter parsing."""
        
        mock_result = {
            'total_sources': 1,
            'successful_updates': 1,
            'failed_updates': 0,
            'skipped_sources': 0
        }
        
        with patch('app.cli.scoring_commands.build_application') as mock_build:
            mock_integration = Mock()
            mock_integration.update_all_source_scores = AsyncMock(return_value=mock_result)
            mock_build.return_value.uow = Mock()
            
            with patch('app.cli.scoring_commands.ScoringIntegrationService', return_value=mock_integration):
                # Test different time window options
                for window in ['48h', '7d', '30d', 'all']:
                    result = cli_runner.invoke(update_all_scores, ['--time-window', window])
                    assert result.exit_code == 0