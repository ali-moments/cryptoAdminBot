"""
Scoring integration service.

This service orchestrates the scoring process, updating scores in the database
and managing the integration between scoring calculations and persistence.
"""

import asyncio
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.core.dto import TimeWindow, ScoreBreakdown
from app.services.statistics import StatisticsService
from app.services.scoring import ScoringService
from app.services.validation import ScoringValidator, ScoringValidationError
from app.database.uow import UnitOfWork


class ScoringIntegrationService:
    """
    Service that integrates scoring calculations with database persistence.
    
    Handles batch score updates, scheduling, and coordination between
    the scoring system and the database.
    """
    
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._statistics_service = StatisticsService(uow)
        self._scoring_service = ScoringService(self._statistics_service)
        self._validator = ScoringValidator()

    async def update_all_source_scores(
        self,
        time_window: TimeWindow | None = None,
        batch_size: int = 10,
    ) -> Dict[str, int]:
        """
        Update scores for all active signal sources.
        
        Args:
            time_window: Time window for scoring calculations
            batch_size: Number of sources to process concurrently
            
        Returns:
            Dictionary with update statistics
        """
        
        results = {
            "total_sources": 0,
            "successful_updates": 0,
            "failed_updates": 0,
            "skipped_sources": 0,
        }
        
        try:
            # Get all active sources
            async with self._uow:
                sources = await self._uow.signal_sources.active()
                results["total_sources"] = len(sources)
            
            if not sources:
                return results
            
            # Process sources in batches to avoid overwhelming the database
            source_batches = [
                sources[i:i + batch_size] 
                for i in range(0, len(sources), batch_size)
            ]
            
            for batch in source_batches:
                await self._process_source_batch(batch, time_window, results)
                
                # Brief pause between batches to be database-friendly
                await asyncio.sleep(0.1)
            
            return results
            
        except Exception as e:
            raise ScoringValidationError(f"Failed to update source scores: {str(e)}")

    async def update_all_source_scores_and_statistics(
        self,
        time_window: TimeWindow | None = None,
        batch_size: int = 10,
    ) -> Dict[str, int]:
        """
        Update both scores and statistics for all active signal sources.
        
        This ensures the database has up-to-date analytics data for admin panel use.
        
        Args:
            time_window: Time window for scoring calculations
            batch_size: Number of sources to process concurrently
            
        Returns:
            Dictionary with update statistics
        """
        
        results = {
            "total_sources": 0,
            "successful_updates": 0,
            "failed_updates": 0,
            "skipped_sources": 0,
            "statistics_updated": 0,
        }
        
        try:
            # Get all active sources
            async with self._uow:
                sources = await self._uow.signal_sources.active()
                results["total_sources"] = len(sources)
            
            if not sources:
                return results
            
            # Process sources in batches to avoid overwhelming the database
            source_batches = [
                sources[i:i + batch_size] 
                for i in range(0, len(sources), batch_size)
            ]
            
            for batch in source_batches:
                await self._process_source_batch_with_statistics(batch, time_window, results)
                
                # Brief pause between batches to be database-friendly
                await asyncio.sleep(0.1)
            
            return results
            
        except Exception as e:
            raise ScoringValidationError(f"Failed to update source scores and statistics: {str(e)}")

    async def update_single_source_score(
        self,
        source_id: int,
        time_window: TimeWindow | None = None,
    ) -> ScoreBreakdown:
        """
        Update score for a single source and return the breakdown.
        
        Args:
            source_id: ID of the source to update
            time_window: Time window for scoring calculations
            
        Returns:
            ScoreBreakdown with the calculated score details
        """
        
        # Calculate the new score
        score_breakdown = await self._scoring_service.calculate_source_score(
            source_id, time_window
        )
        
        # Update the database
        async with self._uow:
            success = await self._uow.signal_sources.update_score(
                source_id, score_breakdown.score
            )
            
            if success:
                await self._uow.commit()
            else:
                raise ScoringValidationError(f"Failed to update score for source {source_id}")
        
        return score_breakdown

    async def bulk_score_update(
        self,
        source_scores: Dict[int, int],
    ) -> int:
        """
        Perform bulk update of source scores.
        
        Args:
            source_scores: Dictionary mapping source_id to new score
            
        Returns:
            Number of sources successfully updated
        """
        
        # Validate all scores first
        validated_scores = {}
        for source_id, score in source_scores.items():
            validated_source_id = self._validator.validate_source_id(source_id)
            validated_score = self._validator.validate_score_value(score)
            validated_scores[validated_source_id] = validated_score
        
        # Perform bulk update
        async with self._uow:
            updated_count = await self._uow.signal_sources.batch_update_scores(
                validated_scores
            )
            
            if updated_count > 0:
                await self._uow.commit()
            
            return updated_count

    async def recalculate_scores_for_time_window(
        self,
        time_window: TimeWindow,
    ) -> Dict[str, any]:
        """
        Recalculate all scores for a specific time window.
        
        Useful for historical analysis or time-based comparisons.
        
        Args:
            time_window: Time window for calculations
            
        Returns:
            Dictionary with recalculation results and statistics
        """
        
        results = {
            "time_window": time_window.name,
            "scores": {},
            "statistics": {
                "total_sources": 0,
                "sources_with_data": 0,
                "average_score": 0.0,
                "highest_score": 0,
                "lowest_score": 1000,
            }
        }
        
        # Get all active sources
        async with self._uow:
            sources = await self._uow.signal_sources.active()
            results["statistics"]["total_sources"] = len(sources)
        
        if not sources:
            return results
        
        scores = []
        sources_with_data = 0
        
        for source in sources:
            try:
                # Calculate score for this time window (don't update database)
                score_breakdown = await self._scoring_service.calculate_source_score(
                    source.id, time_window
                )
                
                results["scores"][source.id] = {
                    "source_name": source.name,
                    "score": score_breakdown.score,
                    "display_score": score_breakdown.display_score,
                    "signal_count": score_breakdown.signal_count,
                }
                
                if score_breakdown.signal_count > 0:
                    scores.append(score_breakdown.score)
                    sources_with_data += 1
                
            except Exception as e:
                # Log error but continue with other sources
                results["scores"][source.id] = {
                    "error": str(e)
                }
        
        # Calculate statistics
        if scores:
            results["statistics"]["sources_with_data"] = sources_with_data
            results["statistics"]["average_score"] = sum(scores) / len(scores)
            results["statistics"]["highest_score"] = max(scores)
            results["statistics"]["lowest_score"] = min(scores)
        
        return results

    async def get_score_update_recommendations(self) -> Dict[str, List[int]]:
        """
        Analyze which sources need score updates based on various criteria.
        
        Returns:
            Dictionary categorizing sources by update priority
        """
        
        recommendations = {
            "high_priority": [],    # Sources with significant changes expected
            "medium_priority": [],  # Sources with moderate activity
            "low_priority": [],     # Sources with minimal recent activity
            "skip": [],            # Sources that don't need updates
        }
        
        async with self._uow:
            sources = await self._uow.signal_sources.active()
        
        for source in sources:
            try:
                # Get recent statistics to determine update priority
                recent_stats = await self._statistics_service.get_source_statistics(
                    source.id, TimeWindow.last_48h()
                )
                
                all_time_stats = await self._statistics_service.get_source_statistics(
                    source.id, TimeWindow.all_time()
                )
                
                # Categorize based on recent activity and completions
                if recent_stats.completed_signals >= 3:
                    recommendations["high_priority"].append(source.id)
                elif recent_stats.completed_signals >= 1:
                    recommendations["medium_priority"].append(source.id)
                elif recent_stats.active_signals > 0:
                    recommendations["low_priority"].append(source.id)
                elif all_time_stats.total_signals == 0:
                    recommendations["skip"].append(source.id)
                else:
                    recommendations["low_priority"].append(source.id)
                    
            except Exception:
                # If we can't analyze, put in low priority
                recommendations["low_priority"].append(source.id)
        
        return recommendations

    async def validate_score_consistency(self) -> Dict[str, any]:
        """
        Validate consistency between stored scores and calculated scores.
        
        Returns:
            Report of any inconsistencies found
        """
        
        report = {
            "total_sources": 0,
            "consistent_sources": 0,
            "inconsistent_sources": 0,
            "inconsistencies": [],
        }
        
        async with self._uow:
            sources = await self._uow.signal_sources.active()
            report["total_sources"] = len(sources)
        
        for source in sources:
            try:
                # Get stored score
                stored_score = source.score
                
                # Calculate current score
                current_breakdown = await self._scoring_service.calculate_source_score(
                    source.id, TimeWindow.all_time()
                )
                calculated_score = current_breakdown.score
                
                # Check for significant differences
                score_diff = abs(stored_score - calculated_score)
                
                if score_diff <= 10:  # Allow small differences due to rounding
                    report["consistent_sources"] += 1
                else:
                    report["inconsistent_sources"] += 1
                    report["inconsistencies"].append({
                        "source_id": source.id,
                        "source_name": source.name,
                        "stored_score": stored_score,
                        "calculated_score": calculated_score,
                        "difference": score_diff,
                        "signal_count": current_breakdown.signal_count,
                    })
                    
            except Exception as e:
                report["inconsistent_sources"] += 1
                report["inconsistencies"].append({
                    "source_id": source.id,
                    "source_name": getattr(source, 'name', 'Unknown'),
                    "error": str(e),
                })
        
        return report

    async def emergency_score_reset(
        self,
        source_id: int,
        reason: str = "Manual reset",
    ) -> bool:
        """
        Emergency reset of a source score to 0.
        
        Use only in cases of data corruption or manual intervention needed.
        """
        
        try:
            async with self._uow:
                success = await self._uow.signal_sources.update_score(source_id, 0)
                
                if success:
                    # Also reset related statistics if needed
                    await self._uow.signal_sources.update_statistics(
                        source_id,
                        score=0,
                        # Could reset other fields here if needed
                    )
                    
                    await self._uow.commit()
                    return True
                
                return False
                
        except Exception:
            return False

    async def _process_source_batch(
        self,
        sources: List,
        time_window: TimeWindow | None,
        results: Dict[str, int],
    ) -> None:
        """Process a batch of sources concurrently."""
        
        tasks = []
        for source in sources:
            task = self._update_single_source_safe(source.id, time_window, results)
            tasks.append(task)
        
        # Process batch concurrently
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_source_batch_with_statistics(
        self,
        sources: List,
        time_window: TimeWindow | None,
        results: Dict[str, int],
    ) -> None:
        """Process a batch of sources concurrently, updating both scores and statistics."""
        
        tasks = []
        for source in sources:
            task = self._update_single_source_with_statistics_safe(source.id, time_window, results)
            tasks.append(task)
        
        # Process batch concurrently
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _update_single_source_safe(
        self,
        source_id: int,
        time_window: TimeWindow | None,
        results: Dict[str, int],
    ) -> None:
        """Safely update a single source score with error handling."""
        
        try:
            await self.update_single_source_score(source_id, time_window)
            results["successful_updates"] += 1
            
        except ScoringValidationError:
            # Expected validation errors
            results["failed_updates"] += 1
            
        except Exception:
            # Unexpected errors
            results["failed_updates"] += 1

    async def _update_single_source_with_statistics_safe(
        self,
        source_id: int,
        time_window: TimeWindow | None,
        results: Dict[str, int],
    ) -> None:
        """Safely update a single source score and statistics with error handling."""
        
        try:
            # Update score
            await self.update_single_source_score(source_id, time_window)
            
            # Update statistics in the database
            await self.update_source_statistics(source_id, time_window)
            
            results["successful_updates"] += 1
            results["statistics_updated"] += 1
            
        except ScoringValidationError:
            # Expected validation errors
            results["failed_updates"] += 1
            
        except Exception:
            # Unexpected errors
            results["failed_updates"] += 1

    async def update_source_statistics(
        self,
        source_id: int,
        time_window: TimeWindow | None = None,
    ) -> bool:
        """
        Update the stored statistics for a signal source in the database.
        
        This ensures the admin panel gets current data from the database
        rather than calculating it on-demand.
        
        Args:
            source_id: ID of the source to update
            time_window: Time window for calculations (defaults to all-time)
            
        Returns:
            True if successful, False otherwise
        """
        
        try:
            # Calculate current statistics
            source_stats = await self._statistics_service.get_source_statistics(
                source_id, time_window or TimeWindow.all_time()
            )
            
            # Update the database with calculated statistics
            async with self._uow:
                success = await self._uow.signal_sources.update_statistics(
                    source_id,
                    total_signals=source_stats.total_signals,
                    winning_signals=source_stats.tp_hit_count,
                    losing_signals=source_stats.stop_loss_count,
                    cancelled_signals=source_stats.cancelled_count,
                    expired_signals=source_stats.expired_count,
                    average_profit=source_stats.average_profit,
                    best_profit=source_stats.best_profit,
                    worst_profit=source_stats.worst_profit,
                )
                
                if success:
                    await self._uow.commit()
                    return True
                    
                return False
                
        except Exception as e:
            logger.error(f"Failed to update statistics for source {source_id}: {e}")
            return False