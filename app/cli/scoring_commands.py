"""
CLI commands for scoring system management.

Provides command-line interface for scoring operations including
updates, validation, reporting, and maintenance tasks.
"""

import asyncio
import sys
from datetime import datetime, timezone
from typing import Optional

import click
from loguru import logger

from app.bootstrap import build_application
from app.core.dto import TimeWindow
from app.services.scoring_integration import ScoringIntegrationService
from app.analytics.reports import AnalyticsReports
from app.analytics.ranking import AnalyticsRanking


@click.group()
def scoring():
    """Scoring system management commands."""
    pass


@scoring.command()
@click.option('--time-window', 
              type=click.Choice(['48h', '7d', '30d', 'all']), 
              default='all',
              help='Time window for scoring calculations')
@click.option('--batch-size', 
              type=int, 
              default=10,
              help='Number of sources to process concurrently')
@click.option('--dry-run', 
              is_flag=True,
              help='Calculate scores but do not update database')
def update_all_scores(time_window: str, batch_size: int, dry_run: bool):
    """Update scores for all active signal sources."""
    
    async def _update_all():
        app = build_application()
        integration_service = ScoringIntegrationService(app.uow)
        
        # Convert time window string to TimeWindow object
        tw_map = {
            '48h': TimeWindow.last_48h(),
            '7d': TimeWindow.last_7d(),
            '30d': TimeWindow.last_30d(),
            'all': TimeWindow.all_time(),
        }
        time_win = tw_map[time_window]
        
        logger.info(f"Starting score update for time window: {time_window}")
        
        if dry_run:
            # For dry run, just calculate scores without updating
            result = await integration_service.recalculate_scores_for_time_window(time_win)
            
            click.echo(f"\n=== DRY RUN RESULTS ===")
            click.echo(f"Time window: {result['time_window']}")
            click.echo(f"Total sources: {result['statistics']['total_sources']}")
            click.echo(f"Sources with data: {result['statistics']['sources_with_data']}")
            
            if result['statistics']['sources_with_data'] > 0:
                click.echo(f"Average score: {result['statistics']['average_score']:.2f}")
                click.echo(f"Highest score: {result['statistics']['highest_score']}")
                click.echo(f"Lowest score: {result['statistics']['lowest_score']}")
            
            # Show top 10 scores
            scores_list = [
                (sid, data['score'], data.get('source_name', f'Source {sid}'))
                for sid, data in result['scores'].items()
                if isinstance(data, dict) and 'score' in data
            ]
            scores_list.sort(key=lambda x: x[1], reverse=True)
            
            click.echo(f"\nTop 10 scores:")
            for i, (sid, score, name) in enumerate(scores_list[:10], 1):
                click.echo(f"{i:2d}. {name[:30]:30s} {score:4d} ({score/100:.2f}/10)")
                
        else:
            # Actual update
            result = await integration_service.update_all_source_scores(
                time_window=time_win,
                batch_size=batch_size
            )
            
            click.echo(f"\n=== UPDATE RESULTS ===")
            click.echo(f"Total sources: {result['total_sources']}")
            click.echo(f"Successful updates: {result['successful_updates']}")
            click.echo(f"Failed updates: {result['failed_updates']}")
            click.echo(f"Skipped sources: {result['skipped_sources']}")
            
            if result['failed_updates'] > 0:
                click.echo(f"⚠️  {result['failed_updates']} sources failed to update")
                sys.exit(1)
            else:
                click.echo(f"✅ All sources updated successfully")
    
    asyncio.run(_update_all())


@scoring.command()
@click.argument('source_id', type=int)
@click.option('--time-window', 
              type=click.Choice(['48h', '7d', '30d', 'all']), 
              default='all',
              help='Time window for scoring calculations')
@click.option('--explain', 
              is_flag=True,
              help='Show detailed score breakdown explanation')
def update_source_score(source_id: int, time_window: str, explain: bool):
    """Update score for a single source."""
    
    async def _update_source():
        app = build_application()
        integration_service = ScoringIntegrationService(app.uow)
        
        tw_map = {
            '48h': TimeWindow.last_48h(),
            '7d': TimeWindow.last_7d(),
            '30d': TimeWindow.last_30d(),
            'all': TimeWindow.all_time(),
        }
        time_win = tw_map[time_window]
        
        try:
            logger.info(f"Updating score for source {source_id}")
            
            breakdown = await integration_service.update_single_source_score(
                source_id, time_win
            )
            
            click.echo(f"\n=== SCORE UPDATE SUCCESSFUL ===")
            click.echo(f"Source ID: {source_id}")
            click.echo(f"New Score: {breakdown.score}/1000 ({breakdown.display_score:.2f}/10)")
            click.echo(f"Signal Count: {breakdown.signal_count}")
            
            if explain:
                from app.services.scoring import ScoringService
                from app.services.statistics import StatisticsService
                
                stats_service = StatisticsService(app.uow)
                scoring_service = ScoringService(stats_service)
                
                explanation = scoring_service.explain_score(breakdown)
                click.echo(f"\n=== DETAILED BREAKDOWN ===")
                click.echo(explanation)
            
        except Exception as e:
            click.echo(f"❌ Failed to update source {source_id}: {str(e)}")
            sys.exit(1)
    
    asyncio.run(_update_source())


@scoring.command()
def validate_consistency():
    """Validate consistency between stored and calculated scores."""
    
    async def _validate():
        app = build_application()
        integration_service = ScoringIntegrationService(app.uow)
        
        logger.info("Validating score consistency...")
        
        report = await integration_service.validate_score_consistency()
        
        click.echo(f"\n=== CONSISTENCY VALIDATION ===")
        click.echo(f"Total sources: {report['total_sources']}")
        click.echo(f"Consistent sources: {report['consistent_sources']}")
        click.echo(f"Inconsistent sources: {report['inconsistent_sources']}")
        
        if report['inconsistent_sources'] == 0:
            click.echo("✅ All scores are consistent!")
        else:
            click.echo(f"\n⚠️  Found {report['inconsistent_sources']} inconsistencies:")
            
            for inconsistency in report['inconsistencies']:
                if 'error' in inconsistency:
                    click.echo(f"  - Source {inconsistency['source_id']}: {inconsistency['error']}")
                else:
                    click.echo(
                        f"  - {inconsistency['source_name']} (ID: {inconsistency['source_id']}): "
                        f"stored={inconsistency['stored_score']}, "
                        f"calculated={inconsistency['calculated_score']}, "
                        f"diff={inconsistency['difference']}"
                    )
    
    asyncio.run(_validate())


@scoring.command()
@click.option('--priority', 
              type=click.Choice(['high', 'medium', 'low', 'all']), 
              default='all',
              help='Show sources with specific update priority')
def update_recommendations(priority: str):
    """Get recommendations for which sources need score updates."""
    
    async def _get_recommendations():
        app = build_application()
        integration_service = ScoringIntegrationService(app.uow)
        
        logger.info("Analyzing update recommendations...")
        
        recommendations = await integration_service.get_score_update_recommendations()
        
        click.echo(f"\n=== UPDATE RECOMMENDATIONS ===")
        
        if priority == 'all' or priority == 'high':
            high_priority = recommendations['high_priority']
            click.echo(f"\nHigh Priority ({len(high_priority)} sources):")
            if high_priority:
                click.echo("  Sources with 3+ recent completions - update immediately")
                for source_id in high_priority:
                    click.echo(f"  - Source {source_id}")
            else:
                click.echo("  None")
        
        if priority == 'all' or priority == 'medium':
            medium_priority = recommendations['medium_priority']
            click.echo(f"\nMedium Priority ({len(medium_priority)} sources):")
            if medium_priority:
                click.echo("  Sources with 1-2 recent completions - update within 24h")
                for source_id in medium_priority:
                    click.echo(f"  - Source {source_id}")
            else:
                click.echo("  None")
        
        if priority == 'all' or priority == 'low':
            low_priority = recommendations['low_priority']
            click.echo(f"\nLow Priority ({len(low_priority)} sources):")
            if low_priority:
                click.echo("  Sources with minimal recent activity - update when convenient")
                click.echo(f"  {len(low_priority)} sources total")
            else:
                click.echo("  None")
        
        if priority == 'all':
            skip = recommendations['skip']
            click.echo(f"\nSkip ({len(skip)} sources):")
            if skip:
                click.echo("  Sources with no signals - no update needed")
            else:
                click.echo("  None")
    
    asyncio.run(_get_recommendations())


@scoring.command()
@click.option('--format', 
              type=click.Choice(['text', 'json']), 
              default='text',
              help='Output format')
@click.option('--output', 
              type=click.Path(),
              help='Output file path (stdout if not specified)')
def generate_report(format: str, output: Optional[str]):
    """Generate comprehensive performance report."""
    
    async def _generate_report():
        app = build_application()
        reports_service = AnalyticsReports(app.uow)
        
        logger.info("Generating executive summary report...")
        
        report = await reports_service.generate_executive_summary()
        
        if format == 'text':
            report_text = reports_service.format_report_as_text(report)
            
            if output:
                with open(output, 'w') as f:
                    f.write(report_text)
                click.echo(f"Report written to {output}")
            else:
                click.echo(report_text)
        
        elif format == 'json':
            import orjson
            
            # Convert report to JSON-serializable format
            report_dict = {
                "title": report.title,
                "generated_at": report.generated_at.isoformat(),
                "time_window": report.time_window,
                "sections": [
                    {
                        "title": section.title,
                        "content": section.content,
                        "format_type": section.format_type,
                    }
                    for section in report.sections
                ],
                "summary": report.summary,
            }
            
            report_json = orjson.dumps(report_dict, option=orjson.OPT_INDENT_2).decode()
            
            if output:
                with open(output, 'w') as f:
                    f.write(report_json)
                click.echo(f"Report written to {output}")
            else:
                click.echo(report_json)
    
    asyncio.run(_generate_report())


@scoring.command()
@click.option('--limit', type=int, default=10, help='Number of top sources to show')
def leaderboard(limit: int):
    """Show current scoring leaderboard."""
    
    async def _show_leaderboard():
        app = build_application()
        ranking_service = AnalyticsRanking(app.uow)
        
        logger.info("Generating leaderboard...")
        
        from app.analytics.ranking import RankingCriteria
        
        leaderboard = await ranking_service.get_score_leaderboard(
            RankingCriteria(metric="score"),
            limit=limit
        )
        
        click.echo(f"\n=== TOP {limit} SIGNAL SOURCES ===")
        click.echo("Rank | Source Name                    | Score    | TP Rate | Signals")
        click.echo("-" * 70)
        
        for source in leaderboard:
            source_name = source.source_name or f"Source {source.source_id}"
            tp_rate = source.score_breakdown.tp_hit_rate * 100
            
            click.echo(
                f"{source.rank:4d} | {source_name[:30]:30s} | "
                f"{source.score_breakdown.display_score:6.2f}/10 | "
                f"{tp_rate:6.1f}% | {source.score_breakdown.signal_count:7d}"
            )
    
    asyncio.run(_show_leaderboard())


@scoring.command()
@click.argument('source_id', type=int)
@click.confirmation_option(prompt='This will reset the source score to 0. Are you sure?')
def emergency_reset(source_id: int):
    """Emergency reset of a source score to 0."""
    
    async def _emergency_reset():
        app = build_application()
        integration_service = ScoringIntegrationService(app.uow)
        
        logger.warning(f"Performing emergency reset for source {source_id}")
        
        success = await integration_service.emergency_score_reset(
            source_id, 
            reason="CLI emergency reset"
        )
        
        if success:
            click.echo(f"✅ Source {source_id} score reset to 0")
        else:
            click.echo(f"❌ Failed to reset source {source_id}")
            sys.exit(1)
    
    asyncio.run(_emergency_reset())


if __name__ == '__main__':
    scoring()