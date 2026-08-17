from zoneinfo import ZoneInfo
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from loguru import logger

from app.config.settings import settings
from app.database.uow import UnitOfWork
from app.services.scoring_integration import ScoringIntegrationService

if TYPE_CHECKING:
    from app.services.telegram import TelegramService
    from app.analytics.pnl import PnlAnalytics


# Global references for job functions
_telegram_service: "TelegramService | None" = None
_pnl_analytics: "PnlAnalytics | None" = None
_scoring_integration_service: "ScoringIntegrationService | None" = None


async def good_morning_job() -> None:
    """Execute good morning message job - plain function for APScheduler serialization."""
    if _telegram_service is None:
        logger.error("Telegram service not available for good morning job")
        return

    try:
        logger.info("Executing good morning job...")
        await _telegram_service.send_good_morning()
        logger.success("Good morning message sent successfully")
    except Exception as e:
        logger.error(f"Failed to send good morning message: {e}")
        # Don't re-raise - scheduler should continue running


async def good_night_job() -> None:
    """Execute good night message job - plain function for APScheduler serialization."""
    if _telegram_service is None:
        logger.error("Telegram service not available for good night job")
        return

    try:
        logger.info("Executing good night job...")
        await _telegram_service.send_good_night()
        logger.success("Good night message sent successfully")
    except Exception as e:
        logger.error(f"Failed to send good night message: {e}")
        # Don't re-raise - scheduler should continue running


async def calculate_24h_pnl_job() -> None:
    """Calculate 24-hour PNL - plain function for APScheduler serialization."""
    if _pnl_analytics is None:
        logger.error("PNL analytics not available for 24h PNL job")
        return

    try:
        logger.info("Calculating 24-hour PNL...")
        pnl = await _pnl_analytics.get_24h_pnl()
        logger.success(f"24h PNL calculated: {len(pnl.items)} items, total: {pnl.total:.2f}%")
        await _telegram_service.send_pnl(pnldto=pnl)
    except Exception as e:
        logger.error(f"Failed to calculate 24h PNL: {e}")
        # Don't re-raise - scheduler should continue running


async def calculate_weekly_pnl_job() -> None:
    """Calculate weekly PNL - plain function for APScheduler serialization."""
    if _pnl_analytics is None:
        logger.error("PNL analytics not available for weekly PNL job")
        return

    try:
        logger.info("Calculating weekly PNL...")
        pnl = await _pnl_analytics.get_weekly_pnl()
        logger.success(f"Weekly PNL calculated: {len(pnl.items)} items, total: {pnl.total:.2f}%")
        await _telegram_service.send_pnl(pnldto=pnl)
    except Exception as e:
        logger.error(f"Failed to calculate weekly PNL: {e}")
        # Don't re-raise - scheduler should continue running


async def update_all_scores_and_analytics_job() -> None:
    """Update all signal source scores and analytics - plain function for APScheduler serialization."""
    if _scoring_integration_service is None:
        logger.error("Scoring integration service not available for score and analytics update job")
        return

    try:
        logger.info("Starting scheduled score and analytics update for all sources...")
        
        # Update all source scores using all-time data
        results = await _scoring_integration_service.update_all_source_scores_and_statistics(
            time_window=None,  # Use all-time data
            batch_size=10,     # Process 10 sources at a time
        )
        
        # Log detailed results
        total = results["total_sources"]
        successful = results["successful_updates"]
        failed = results["failed_updates"]
        skipped = results["skipped_sources"]
        statistics_updated = results.get("statistics_updated", 0)
        
        logger.success(
            f"Score and analytics update completed: {successful}/{total} sources updated successfully"
        )
        
        if statistics_updated > 0:
            logger.info(f"{statistics_updated} sources had their analytics statistics updated")
        
        if failed > 0:
            logger.warning(f"{failed} sources failed to update")
            
        if skipped > 0:
            logger.info(f"{skipped} sources were skipped")
            
        # Log summary statistics
        if total > 0:
            success_rate = (successful / total) * 100
            logger.info(f"Score and analytics update summary: {success_rate:.1f}% success rate")
                
    except Exception as e:
        logger.error(f"Failed to update source scores and analytics: {e}")
        # Don't re-raise - scheduler should continue running


class AppScheduler:
    """
    Application scheduler using APScheduler with PostgreSQL persistence.

    Schedules daily good morning and good night messages using Asia/Tehran timezone.
    Jobs are persisted in PostgreSQL and survive application restarts.
    """

    def __init__(
        self,
        telegram_service: "TelegramService",
        pnl_analytics: "PnlAnalytics",
    ) -> None:
        self._telegram_service = telegram_service
        self._pnl_analytics = pnl_analytics
        self._scheduler: AsyncIOScheduler | None = None

    async def start(self) -> None:
        """Initialize and start the scheduler with persistent job store."""
        global _telegram_service, _pnl_analytics, _scoring_integration_service

        if self._scheduler is not None:
            logger.warning("Scheduler is already started")
            return

        logger.info("Starting scheduler...")

        # Set global service references for job functions
        _telegram_service = self._telegram_service
        _pnl_analytics = self._pnl_analytics
        
        # Initialize scoring integration service
        _scoring_integration_service = ScoringIntegrationService(UnitOfWork)

        # Create synchronous database URL for APScheduler (it doesn't support async drivers)
        sync_db_url = settings.alembic_database_url
        if "+asyncpg" in sync_db_url:
            # Replace asyncpg with psycopg for synchronous SQLAlchemy usage
            sync_db_url = sync_db_url.replace("+asyncpg", "+psycopg")

        # Configure PostgreSQL job store using synchronous database connection
        jobstores = {
            'default': SQLAlchemyJobStore(url=sync_db_url)
        }

        # Configure async executor
        executors = {
            'default': AsyncIOExecutor()
        }

        # Job defaults
        job_defaults = {
            'coalesce': False,  # Don't combine missed jobs
            'max_instances': 1,  # Only one instance of each job at a time
            'misfire_grace_time': 300,  # 5 minutes grace time for missed jobs
        }

        # Create scheduler with Asia/Tehran timezone
        self._scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone=ZoneInfo("Asia/Tehran")
        )

        # Register scheduled jobs
        await self._register_jobs()

        # Start the scheduler
        self._scheduler.start()

        logger.success("Scheduler started successfully")

    async def stop(self) -> None:
        """Stop the scheduler cleanly."""
        global _telegram_service, _pnl_analytics, _scoring_integration_service

        if self._scheduler is None:
            logger.warning("Scheduler is not running")
            return

        logger.info("Stopping scheduler...")

        try:
            self._scheduler.shutdown(wait=True)
            self._scheduler = None
            # Clear global references
            _telegram_service = None
            _pnl_analytics = None
            _scoring_integration_service = None
            logger.success("Scheduler stopped successfully")
        except Exception as e:
            logger.error(f"Error stopping scheduler: {e}")
            raise

    async def _register_jobs(self) -> None:
        """Register all scheduled jobs with stable IDs to prevent duplicates."""
        if self._scheduler is None:
            raise RuntimeError("Scheduler not initialized")

        logger.info("Registering scheduled jobs...")

        # Good morning job - 08:00 Asia/Tehran daily
        # Use plain function instead of bound method for proper serialization
        self._scheduler.add_job(
            func=good_morning_job,
            trigger='cron',
            hour=5,
            minute=0,
            id='daily_good_morning',
            replace_existing=True,  # Replace existing job on restart
            name='Daily Good Morning Message'
        )

        # Good night job - 23:30 Asia/Tehran daily
        # Use plain function instead of bound method for proper serialization
        self._scheduler.add_job(
            func=good_night_job,
            trigger='cron',
            hour=22,
            minute=0,
            id='daily_good_night',
            replace_existing=True,  # Replace existing job on restart
            name='Daily Good Night Message'
        )

        # 24-hour PNL job - every day at 21:50
        self._scheduler.add_job(
            func=calculate_24h_pnl_job,
            trigger='cron',
            hour=21,
            minute=50,
            id='daily_24h_pnl',
            replace_existing=True,
            name='Daily 24h PNL Calculation'
        )

        # Weekly PNL job — Fridays at 21:55 only
        self._scheduler.add_job(
            func=calculate_weekly_pnl_job,
            trigger='cron',
            day_of_week='fri',
            hour=21,
            minute=55,
            id='periodic_weekly_pnl',
            replace_existing=True,
            name='Weekly PNL Calculation(Fridays only)'
        )

        # Score and analytics update job - every 6 hours at 00:00, 06:00, 12:00, 18:00
        self._scheduler.add_job(
            func=update_all_scores_and_analytics_job,
            trigger='cron',
            hour='*/6',
            minute=0,
            id='score_analytics_update_6h',
            replace_existing=True,
            name='Score and Analytics Update Every 6 Hours'
        )

        logger.info("Scheduled jobs registered successfully")
