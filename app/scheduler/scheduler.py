import asyncio
from zoneinfo import ZoneInfo
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from loguru import logger

from app.config.settings import settings
from app.scheduler.scoring_scheduler import setup_scoring_jobs, remove_scoring_jobs

if TYPE_CHECKING:
    from app.services.telegram import TelegramService
    from app.bootstrap import Application


# Global reference to telegram service for job functions
_telegram_service: "TelegramService | None" = None


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


class AppScheduler:
    """
    Application scheduler using APScheduler with PostgreSQL persistence.

    Schedules daily good morning and good night messages using Asia/Tehran timezone.
    Jobs are persisted in PostgreSQL and survive application restarts.
    """

    def __init__(
        self,
        telegram_service: "TelegramService",
        app: "Application | None" = None
    ) -> None:
        self._telegram_service = telegram_service
        self._app = app
        self._scheduler: AsyncIOScheduler | None = None

    async def start(self) -> None:
        """Initialize and start the scheduler with persistent job store."""
        global _telegram_service

        if self._scheduler is not None:
            logger.warning("Scheduler is already started")
            return

        logger.info("Starting scheduler...")

        # Set global telegram service reference for job functions
        _telegram_service = self._telegram_service

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

        # Setup scoring jobs if app is available
        if self._app is not None:
            setup_scoring_jobs(self._scheduler, self._app)

        # Start the scheduler
        self._scheduler.start()

        logger.success("Scheduler started successfully")

    async def stop(self) -> None:
        """Stop the scheduler cleanly."""
        global _telegram_service

        if self._scheduler is None:
            logger.warning("Scheduler is not running")
            return

        logger.info("Stopping scheduler...")

        try:
            # Remove scoring jobs if they exist
            if self._scheduler is not None:
                remove_scoring_jobs(self._scheduler)

            self._scheduler.shutdown(wait=True)
            self._scheduler = None
            # Clear global reference
            _telegram_service = None
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

        logger.info("Scheduled jobs registered successfully")
