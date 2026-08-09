"""
Statistics service for calculating signal source performance metrics.

This service is responsible for calculating reusable performance statistics
that are consumed by both the scoring system and analytics modules.
"""

import math
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from sqlalchemy import select, func, and_, or_

from app.core.dto import SignalStatistics, TimeWindow
from app.database.enums import SignalStatus, TrackingStatus, CloseReason
from app.database.models import SignalSource, Signal, Tracking, TpHit
from app.database.uow import UnitOfWork
from app.services.validation import ScoringValidator, ScoringValidationError


class StatisticsService:
    """Service for calculating signal source statistics."""
    
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._validator = ScoringValidator()

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
                    # Log warnings but don't fail
                    # In a real implementation, you'd use proper logging
                    pass
                
                return validated_stats
                
        except Exception as e:
            if isinstance(e, ScoringValidationError):
                raise
            
            # Handle unexpected errors gracefully
            raise ScoringValidationError(f"Failed to calculate statistics for source {source_id}: {str(e)}")

    async def get_all_sources_statistics(
        self,
        time_window: TimeWindow | None = None,
    ) -> dict[int, SignalStatistics]:
        """Get statistics for all active sources."""
        
        async with self._uow:
            sources = await self._uow.signal_sources.active()
            
            statistics = {}
            for source in sources:
                stats = await self.get_source_statistics(source.id, time_window)
                statistics[source.id] = stats
            
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
        if total == 0:
            return Decimal("0.0000")
        
        return Decimal(count) / Decimal(total)

    async def calculate_confidence_score(self, signal_count: int) -> float:
        """
        Calculate confidence score based on sample size.
        
        Formula: min(1, sqrt(signal_count / 100))
        
        Examples:
        - 1 signal -> 0.10
        - 25 signals -> 0.50  
        - 100 signals -> 1.00
        - 500 signals -> 1.00 (capped at 1.00)
        """
        if signal_count <= 0:
            return 0.0
        
        return min(1.0, math.sqrt(signal_count / 100.0))

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
            time_filter = self._build_time_filter(time_window)
            
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