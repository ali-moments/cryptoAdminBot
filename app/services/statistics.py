"""
Statistics service for calculating signal source performance metrics.

This service is responsible for calculating reusable performance statistics
that are consumed by both the scoring system and analytics modules.
"""

import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from loguru import logger
from sqlalchemy import select, func

from app.core.dto import SignalStatistics, TimeWindow
from app.database.enums import SignalStatus, CloseReason
from app.database.models import Signal, Tracking, TpHit
from app.database.uow import UnitOfWork
from app.services.validation import ScoringValidator, ScoringValidationError
from app.analytics.utils import MathUtils, PerformanceMonitor


class StatisticsService:
    """Service for calculating signal source statistics."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._validator = ScoringValidator()

    @PerformanceMonitor.monitor_performance("get_source_statistics")
    async def get_source_statistics(
        self,
        source_id: int,
        time_window: TimeWindow | None = None,
    ) -> SignalStatistics:
        """Calculate comprehensive statistics for a signal source."""

        # Validate inputs
        source_id = self._validator.validate_source_id(source_id)
        time_window = self._validator.validate_time_window(time_window)

        try:
            async with self._uow:
                # Base query for signals in time window
                time_filter = self._build_time_filter(time_window)

                # Get basic signal counts
                total_signals = await self._count_total_signals(source_id, time_filter)
                completed_signals = await self._count_completed_signals(source_id, time_filter)
                active_signals = await self._count_active_signals(source_id, time_filter)

                # Get TP hit and stop loss counts
                tp_hit_count = await self._count_tp_hit_signals(source_id, time_filter)
                stop_loss_count = await self._count_stop_loss_signals(source_id, time_filter)
                cancelled_count = await self._count_cancelled_signals(source_id, time_filter)
                expired_count = await self._count_expired_signals(source_id, time_filter)

                # Calculate rates with safe division
                tp_hit_rate = self._validator.handle_division_by_zero_cases(
                    Decimal(tp_hit_count), completed_signals
                )
                stop_loss_rate = self._validator.handle_division_by_zero_cases(
                    Decimal(stop_loss_count), completed_signals
                )

                # Get profit statistics
                profit_stats = await self._calculate_profit_statistics(source_id, time_filter)

                # Create statistics object
                stats = SignalStatistics(
                    source_id=source_id,
                    total_signals=total_signals,
                    completed_signals=completed_signals,
                    active_signals=active_signals,
                    tp_hit_count=tp_hit_count,
                    stop_loss_count=stop_loss_count,
                    cancelled_count=cancelled_count,
                    expired_count=expired_count,
                    tp_hit_rate=tp_hit_rate,
                    stop_loss_rate=stop_loss_rate,
                    total_profit=profit_stats["total_profit"],
                    average_profit=profit_stats["average_profit"],
                    best_profit=profit_stats["best_profit"],
                    worst_profit=profit_stats["worst_profit"],
                    profitable_signal_count=profit_stats["profitable_count"],
                    losing_signal_count=profit_stats["losing_count"],
                )

                # Validate and sanitize the result
                validated_stats = self._validator.validate_source_statistics(stats)

                # Check for data quality warnings
                warnings = self._validator.check_data_quality_warnings(validated_stats)
                if warnings:
                    logger.warning(
                        f"Data quality issues for source {source_id}: {', '.join(warnings)}"
                    )

                return validated_stats

        except Exception as e:
            if isinstance(e, ScoringValidationError):
                raise

            # Handle unexpected errors gracefully
            raise ScoringValidationError(f"Failed to calculate statistics for source {source_id}: {str(e)}")

    @PerformanceMonitor.monitor_performance("get_all_sources_statistics_batch")
    async def get_all_sources_statistics(
        self,
        time_window: TimeWindow | None = None,
    ) -> dict[int, SignalStatistics]:
        """Get statistics for all active sources using batch queries."""

        async with self._uow:
            sources = await self._uow.signal_sources.active()
            
            if not sources:
                return {}
            
            source_ids = [source.id for source in sources]
            time_filter = self._build_time_filter(time_window)
            
            # Initialize batch tracker for monitoring progress
            batch_tracker = PerformanceMonitor.track_batch_operation(
                "batch_statistics_calculation", 
                len(source_ids),
                batch_size=50  # Report every 50 sources
            )
            
            # Batch queries for all sources at once
            statistics = {}
            
            # Get all counts in batch queries
            total_signals_batch = await self._count_total_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)  # 1 of 8 batch queries
            
            completed_signals_batch = await self._count_completed_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)
            
            active_signals_batch = await self._count_active_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)
            
            tp_hit_signals_batch = await self._count_tp_hit_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)
            
            stop_loss_signals_batch = await self._count_stop_loss_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)
            
            cancelled_signals_batch = await self._count_cancelled_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)
            
            expired_signals_batch = await self._count_expired_signals_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)
            
            # Get profit statistics in batch
            profit_stats_batch = await self._calculate_profit_statistics_batch(source_ids, time_filter)
            batch_tracker.update_progress(1)  # Complete batch queries phase
            
            # Build statistics for each source
            processed_count = 0
            for source_id in source_ids:
                total_signals = total_signals_batch.get(source_id, 0)
                completed_signals = completed_signals_batch.get(source_id, 0)
                active_signals = active_signals_batch.get(source_id, 0)
                tp_hit_count = tp_hit_signals_batch.get(source_id, 0)
                stop_loss_count = stop_loss_signals_batch.get(source_id, 0)
                cancelled_count = cancelled_signals_batch.get(source_id, 0)
                expired_count = expired_signals_batch.get(source_id, 0)
                
                # Calculate rates with safe division
                tp_hit_rate = self._validator.handle_division_by_zero_cases(
                    Decimal(tp_hit_count), completed_signals
                )
                stop_loss_rate = self._validator.handle_division_by_zero_cases(
                    Decimal(stop_loss_count), completed_signals
                )
                
                # Get profit statistics for this source
                profit_stats = profit_stats_batch.get(source_id, {
                    "total_profit": Decimal("0.0000"),
                    "average_profit": Decimal("0.0000"),
                    "best_profit": None,
                    "worst_profit": None,
                    "profitable_count": 0,
                    "losing_count": 0,
                })
                
                # Create statistics object
                stats = SignalStatistics(
                    source_id=source_id,
                    total_signals=total_signals,
                    completed_signals=completed_signals,
                    active_signals=active_signals,
                    tp_hit_count=tp_hit_count,
                    stop_loss_count=stop_loss_count,
                    cancelled_count=cancelled_count,
                    expired_count=expired_count,
                    tp_hit_rate=tp_hit_rate,
                    stop_loss_rate=stop_loss_rate,
                    total_profit=profit_stats["total_profit"],
                    average_profit=profit_stats["average_profit"],
                    best_profit=profit_stats["best_profit"],
                    worst_profit=profit_stats["worst_profit"],
                    profitable_signal_count=profit_stats["profitable_count"],
                    losing_signal_count=profit_stats["losing_count"],
                )
                
                # Validate and sanitize the result
                validated_stats = self._validator.validate_source_statistics(stats)
                statistics[source_id] = validated_stats
                
                processed_count += 1
                # Update batch tracker periodically
                if processed_count % 50 == 0:
                    batch_tracker.update_progress(50)
            
            # Complete any remaining items
            if processed_count % 50 != 0:
                batch_tracker.update_progress(processed_count % 50)
            
            # Complete batch tracking
            batch_tracker.complete()

            return statistics

    def _build_time_filter(self, time_window: TimeWindow | None) -> datetime | None:
        """Build time filter for queries."""
        if time_window is None or time_window.hours is None:
            return None

        return datetime.now(timezone.utc) - timedelta(hours=time_window.hours)

    async def _count_total_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count total signals for source."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(Signal.source_id == source_id)
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _count_completed_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count completed (decided) signals - those that are CLOSED."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.CLOSED
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _count_active_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count active signals."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.source_id == source_id,
                Signal.status.in_((SignalStatus.WAITING_ENTRY, SignalStatus.TRACKING))
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _count_tp_hit_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count signals that hit at least one TP target."""
        stmt = (
            select(func.count(func.distinct(Signal.id)))
            .select_from(Signal)
            .join(Tracking)
            .join(TpHit)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.CLOSED
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _count_stop_loss_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count signals that hit stop loss."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .join(Tracking)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.CLOSED,
                Tracking.close_reason.in_((
                    CloseReason.ORIGINAL_STOP_LOSS,
                    CloseReason.MOVED_STOP_LOSS,
                ))
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _count_cancelled_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count cancelled signals."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.CANCELLED
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _count_expired_signals(self, source_id: int, time_filter: datetime | None) -> int:
        """Count expired signals."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.EXPIRED
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalar(stmt)
        return result or 0

    async def _calculate_profit_statistics(
        self,
        source_id: int,
        time_filter: datetime | None,
    ) -> dict:
        """Calculate profit-related statistics."""

        # Get all completed tracking records with profit data
        stmt = (
            select(Tracking.profit_percent)
            .select_from(Signal)
            .join(Tracking)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.CLOSED,
                Tracking.profit_percent.is_not(None)
            )
        )

        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)

        result = await self._uow.session.scalars(stmt)
        profits = [profit for profit in result if profit is not None]

        if not profits:
            return {
                "total_profit": Decimal("0.0000"),
                "average_profit": Decimal("0.0000"),
                "best_profit": None,
                "worst_profit": None,
                "profitable_count": 0,
                "losing_count": 0,
            }

        total_profit = sum(profits)
        average_profit = total_profit / len(profits)
        best_profit = max(profits)
        worst_profit = min(profits)

        profitable_count = sum(1 for p in profits if p > 0)
        losing_count = sum(1 for p in profits if p < 0)

        return {
            "total_profit": total_profit,
            "average_profit": average_profit,
            "best_profit": best_profit,
            "worst_profit": worst_profit,
            "profitable_count": profitable_count,
            "losing_count": losing_count,
        }

    def _calculate_rate(self, count: int, total: int) -> Decimal:
        """Calculate a rate as a decimal between 0 and 1."""
        return MathUtils.safe_divide(count, total)

    async def calculate_confidence_score(self, signal_count: int) -> float:
        """
        Calculate confidence score based on sample size.
        Uses the utility function for consistent calculation across the system.
        """
        return MathUtils.calculate_confidence_score(signal_count)

    async def get_profit_percentiles(
        self,
        time_window: TimeWindow | None = None,
    ) -> dict[str, list[Decimal]]:
        """
        Get profit percentiles for robust normalization.

        Returns total profits and average profits for all sources
        to enable percentile-based scoring.
        """
        async with self._uow:
            # Get all source profit data
            sources = await self._uow.signal_sources.active()

            total_profits = []
            average_profits = []
            best_profits = []

            for source in sources:
                stats = await self.get_source_statistics(source.id, time_window)
                if stats.completed_signals > 0:
                    total_profits.append(stats.total_profit)
                    average_profits.append(stats.average_profit)
                    if stats.best_profit is not None:
                        best_profits.append(stats.best_profit)

            return {
                "total_profits": total_profits,
                "average_profits": average_profits,
                "best_profits": best_profits,
            }
    # Batch query methods for improved performance
    
    async def _count_total_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count total signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count())
            .select_from(Signal)
            .where(Signal.source_id.in_(source_ids))
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _count_completed_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count completed signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count())
            .select_from(Signal)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status == SignalStatus.CLOSED
            )
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _count_active_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count active signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count())
            .select_from(Signal)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status.in_((SignalStatus.WAITING_ENTRY, SignalStatus.TRACKING))
            )
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _count_tp_hit_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count TP hit signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count(func.distinct(Signal.id)))
            .select_from(Signal)
            .join(Tracking)
            .join(TpHit)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status == SignalStatus.CLOSED
            )
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _count_stop_loss_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count stop loss signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count())
            .select_from(Signal)
            .join(Tracking)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status == SignalStatus.CLOSED,
                Tracking.close_reason.in_((
                    CloseReason.ORIGINAL_STOP_LOSS,
                    CloseReason.MOVED_STOP_LOSS,
                ))
            )
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _count_cancelled_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count cancelled signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count())
            .select_from(Signal)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status == SignalStatus.CANCELLED
            )
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _count_expired_signals_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, int]:
        """Count expired signals for multiple sources in a single query."""
        stmt = (
            select(Signal.source_id, func.count())
            .select_from(Signal)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status == SignalStatus.EXPIRED
            )
            .group_by(Signal.source_id)
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        return dict(result.all())
    
    async def _calculate_profit_statistics_batch(self, source_ids: list[int], time_filter: datetime | None) -> dict[int, dict]:
        """Calculate profit statistics for multiple sources efficiently."""
        
        # Get all profit data for all sources in one query
        stmt = (
            select(Signal.source_id, Tracking.profit_percent)
            .select_from(Signal)
            .join(Tracking)
            .where(
                Signal.source_id.in_(source_ids),
                Signal.status == SignalStatus.CLOSED,
                Tracking.profit_percent.is_not(None)
            )
        )
        
        if time_filter:
            stmt = stmt.where(Signal.created_at >= time_filter)
        
        result = await self._uow.session.execute(stmt)
        
        # Group profits by source_id
        profits_by_source = {}
        for source_id, profit in result.all():
            if source_id not in profits_by_source:
                profits_by_source[source_id] = []
            profits_by_source[source_id].append(profit)
        
        # Calculate statistics for each source
        statistics_by_source = {}
        for source_id in source_ids:
            profits = profits_by_source.get(source_id, [])
            
            if not profits:
                statistics_by_source[source_id] = {
                    "total_profit": Decimal("0.0000"),
                    "average_profit": Decimal("0.0000"),
                    "best_profit": None,
                    "worst_profit": None,
                    "profitable_count": 0,
                    "losing_count": 0,
                }
            else:
                total_profit = sum(profits)
                average_profit = total_profit / len(profits)
                best_profit = max(profits)
                worst_profit = min(profits)
                
                profitable_count = sum(1 for p in profits if p > 0)
                losing_count = sum(1 for p in profits if p < 0)
                
                statistics_by_source[source_id] = {
                    "total_profit": total_profit,
                    "average_profit": average_profit,
                    "best_profit": best_profit,
                    "worst_profit": worst_profit,
                    "profitable_count": profitable_count,
                    "losing_count": losing_count,
                }
        
        return statistics_by_source