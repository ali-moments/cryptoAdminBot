"""
Scoring scheduler integration.

Adds scoring-related jobs to the existing scheduler system for
automatic score updates and maintenance tasks.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from loguru import logger

from app.core.dto import TimeWindow
from app.services.scoring_integration import ScoringIntegrationService

if TYPE_CHECKING:
    from app.bootstrap import Application


# Global reference to application for job functions
_app: "Application | None" = None


async def hourly_score_update_job() -> None:
    """Update scores for high-priority sources every hour."""
    if _app is None:
        logger.error("Application not available for hourly score update")
        return
    
    try:
        logger.info("Starting hourly score update...")
        
        integration_service = ScoringIntegrationService(_app.uow)
        
        # Get update recommendations
        recommendations = await integration_service.get_score_update_recommendations()
        
        # Update high-priority sources
        high_priority_sources = recommendations['high_priority']
        
        if not high_priority_sources:
            logger.info("No high-priority sources need updates")
            return
        
        # Update scores for high-priority sources
        score_updates = {}
        for source_id in high_priority_sources:
            try:
                breakdown = await integration_service.update_single_source_score(
                    source_id, TimeWindow.all_time()
                )
                score_updates[source_id] = breakdown.score
                
            except Exception as e:
                logger.error(f"Failed to update source {source_id}: {e}")
        
        if score_updates:
            logger.success(f"Updated scores for {len(score_updates)} high-priority sources")
        else:
            logger.warning("No scores were successfully updated")
            
    except Exception as e:
        logger.error(f"Hourly score update job failed: {e}")


async def daily_score_update_job() -> None:
    """Update scores for all sources daily."""
    if _app is None:
        logger.error("Application not available for daily score update")
        return
    
    try:
        logger.info("Starting daily score update...")
        
        integration_service = ScoringIntegrationService(_app.uow)
        
        # Update all source scores
        result = await integration_service.update_all_source_scores(
            time_window=TimeWindow.all_time(),
            batch_size=5  # Conservative batch size for daily job
        )
        
        logger.success(
            f"Daily score update completed: {result['successful_updates']} successful, "
            f"{result['failed_updates']} failed, {result['skipped_sources']} skipped"
        )
        
        if result['failed_updates'] > 0:
            logger.warning(f"{result['failed_updates']} sources failed to update")
        
    except Exception as e:
        logger.error(f"Daily score update job failed: {e}")


async def weekly_score_validation_job() -> None:
    """Weekly validation of score consistency."""
    if _app is None:
        logger.error("Application not available for weekly validation")
        return
    
    try:
        logger.info("Starting weekly score validation...")
        
        integration_service = ScoringIntegrationService(_app.uow)
        
        # Validate score consistency
        report = await integration_service.validate_score_consistency()
        
        logger.info(
            f"Score validation completed: {report['consistent_sources']} consistent, "
            f"{report['inconsistent_sources']} inconsistent out of {report['total_sources']} total"
        )
        
        if report['inconsistent_sources'] > 0:
            logger.warning(
                f"Found {report['inconsistent_sources']} inconsistent scores - "
                f"manual review recommended"
            )
            
            # Log details of inconsistencies
            for inconsistency in report['inconsistencies'][:5]:  # Log first 5
                if 'error' in inconsistency:
                    logger.error(f"Source {inconsistency['source_id']}: {inconsistency['error']}")
                else:
                    logger.warning(
                        f"Source {inconsistency['source_id']} ({inconsistency.get('source_name', 'Unknown')}): "
                        f"stored={inconsistency['stored_score']}, "
                        f"calculated={inconsistency['calculated_score']}"
                    )
        
    except Exception as e:
        logger.error(f"Weekly score validation job failed: {e}")


def setup_scoring_jobs(scheduler, app: "Application") -> None:
    """
    Add scoring-related jobs to the existing scheduler.
    
    Args:
        scheduler: The APScheduler instance
        app: Application instance with dependencies
    """
    global _app
    _app = app
    
    logger.info("Setting up scoring scheduler jobs...")
    
    # Hourly score update for high-priority sources (every hour during active hours)
    scheduler.add_job(
        func=hourly_score_update_job,
        trigger='cron',
        minute=15,  # Run at :15 past each hour
        hour='8-23',  # Only during active hours (8 AM to 11 PM)
        id='hourly_score_update',
        replace_existing=True,
        name='Hourly High-Priority Score Update'
    )
    
    # Daily comprehensive score update (early morning)
    scheduler.add_job(
        func=daily_score_update_job,
        trigger='cron',
        hour=6,  # 6:00 AM Tehran time
        minute=0,
        id='daily_score_update',
        replace_existing=True,
        name='Daily Comprehensive Score Update'
    )
    
    # Weekly score validation (Sunday morning)
    scheduler.add_job(
        func=weekly_score_validation_job,
        trigger='cron',
        day_of_week='sun',  # Sunday
        hour=7,
        minute=0,
        id='weekly_score_validation',
        replace_existing=True,
        name='Weekly Score Consistency Validation'
    )
    
    logger.success("Scoring scheduler jobs configured successfully")


def remove_scoring_jobs(scheduler) -> None:
    """Remove scoring jobs from scheduler."""
    global _app
    
    job_ids = [
        'hourly_score_update',
        'daily_score_update', 
        'weekly_score_validation'
    ]
    
    for job_id in job_ids:
        try:
            scheduler.remove_job(job_id)
            logger.info(f"Removed scheduling job: {job_id}")
        except Exception:
            # Job might not exist
            pass
    
    _app = None
    logger.info("Scoring scheduler jobs removed")