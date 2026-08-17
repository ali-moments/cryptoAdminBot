"""
CLI command to manually update all source scores and analytics.

This provides a way to trigger score and analytics updates outside of the scheduled job,
useful for testing and manual maintenance. Updates both scores and database statistics
to ensure the admin panel has current data.
"""

import asyncio
from loguru import logger

from app.database.uow import UnitOfWork
from app.services.scoring_integration import ScoringIntegrationService


async def update_all_scores_and_analytics() -> None:
    """Update all source scores and analytics manually."""
    logger.info("Starting manual score and analytics update for all sources...")
    
    try:
        # Initialize services
        uow = UnitOfWork()
        scoring_integration_service = ScoringIntegrationService(uow)
        
        # Update all source scores and statistics
        results = await scoring_integration_service.update_all_source_scores_and_statistics(
            time_window=None,  # Use all-time data
            batch_size=10,     # Process 10 sources at a time
        )
        
        # Log detailed results
        total = results["total_sources"]
        successful = results["successful_updates"]
        failed = results["failed_updates"]
        skipped = results["skipped_sources"]
        statistics_updated = results.get("statistics_updated", 0)
        
        logger.success(f"Manual score and analytics update completed: {successful}/{total} sources updated successfully")
        
        if statistics_updated > 0:
            logger.info(f"{statistics_updated} sources had their analytics statistics updated")
        
        if failed > 0:
            logger.warning(f"{failed} sources failed to update")
            
        if skipped > 0:
            logger.info(f"{skipped} sources were skipped")
            
        # Log summary statistics
        if total > 0:
            success_rate = (successful / total) * 100
            logger.info(f"Manual score and analytics update summary: {success_rate:.1f}% success rate")
            
        return results
        
    except Exception as e:
        logger.error(f"Failed to update source scores and analytics manually: {e}")
        raise


def main():
    """CLI entry point for manual score and analytics updates."""
    asyncio.run(update_all_scores_and_analytics())


if __name__ == "__main__":
    main()