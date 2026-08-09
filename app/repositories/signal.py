from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select, and_
from sqlalchemy.orm import selectinload

from app.database.enums import SignalStatus, Direction, CloseReason
from app.database.models import Signal, Tracking, TpHit
from app.repositories.base import BaseRepository


class SignalRepository(BaseRepository[Signal]):
    model = Signal

    async def get_full(self, signal_id: int) -> Signal | None:
        stmt = (
            select(Signal)
            .where(Signal.id == signal_id)
            .options(
                selectinload(Signal.source),
                selectinload(Signal.entries),
                selectinload(Signal.targets),
                selectinload(Signal.tracking),
            )
        )

        return await self.session.scalar(stmt)

    async def get_active(self) -> list[Signal]:
        stmt = (
            select(Signal)
            .where(
                Signal.status.in_(
                    (
                        SignalStatus.WAITING_ENTRY,
                        SignalStatus.TRACKING,
                    )
                )
            )
            .options(
                selectinload(Signal.entries),
                selectinload(Signal.targets),
                selectinload(Signal.source),
                selectinload(Signal.tracking),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def get_expired(
        self,
        now: datetime,
    ) -> list[Signal]:
        stmt = (
            select(Signal)
            .where(
                Signal.expires_at <= now,
                Signal.status.in_(
                    (
                        SignalStatus.WAITING_ENTRY,
                        SignalStatus.TRACKING,
                    )
                ),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def active_count(self) -> int:
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.status.in_(
                    (
                        SignalStatus.WAITING_ENTRY,
                        SignalStatus.TRACKING,
                    )
                )
            )
        )

        return (await self.session.scalar(stmt)) or 0

    async def published_last_hour(
        self,
        since: datetime,
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(Signal.created_at >= since)
        )

        return (await self.session.scalar(stmt)) or 0

    async def find_active_candidates(
        self,
        symbol: str,
        direction: Direction,
    ) -> list[Signal]:
        stmt = (
            select(Signal)
            .where(
                Signal.symbol == symbol,
                Signal.direction == direction,
                Signal.status.in_(
                    (
                        SignalStatus.WAITING_ENTRY,
                        SignalStatus.TRACKING,
                    )
                ),
            )
            .options(
                selectinload(Signal.entries),
                selectinload(Signal.targets),
                selectinload(Signal.tracking),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)
    # === Scoring-specific queries ===

    async def count_by_source_and_status(
        self,
        source_id: int,
        status: SignalStatus,
        since: datetime | None = None,
    ) -> int:
        """Count signals by source and status, optionally filtered by time."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.source_id == source_id,
                Signal.status == status,
            )
        )
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        return (await self.session.scalar(stmt)) or 0

    async def count_by_source_and_statuses(
        self,
        source_id: int,
        statuses: list[SignalStatus],
        since: datetime | None = None,
    ) -> int:
        """Count signals by source matching any of the given statuses."""
        stmt = (
            select(func.count())
            .select_from(Signal)
            .where(
                Signal.source_id == source_id,
                Signal.status.in_(statuses),
            )
        )
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        return (await self.session.scalar(stmt)) or 0

    async def count_tp_hit_signals_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> int:
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
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        return (await self.session.scalar(stmt)) or 0

    async def count_stop_loss_signals_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> int:
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
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        return (await self.session.scalar(stmt)) or 0

    async def get_profit_data_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> list[Decimal]:
        """Get all profit percentages for completed signals from a source."""
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
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        result = await self.session.scalars(stmt)
        return [profit for profit in result if profit is not None]

    async def get_profit_statistics_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> dict[str, Decimal]:
        """Get aggregated profit statistics for a source."""
        stmt = (
            select(
                func.sum(Tracking.profit_percent).label('total_profit'),
                func.avg(Tracking.profit_percent).label('avg_profit'),
                func.max(Tracking.profit_percent).label('best_profit'),
                func.min(Tracking.profit_percent).label('worst_profit'),
                func.count().label('count'),
            )
            .select_from(Signal)
            .join(Tracking)
            .where(
                Signal.source_id == source_id,
                Signal.status == SignalStatus.CLOSED,
                Tracking.profit_percent.is_not(None)
            )
        )
        
        if since:
            stmt = stmt.where(Signal.created_at >= since)
        
        result = await self.session.execute(stmt)
        row = result.first()
        
        return {
            'total_profit': row.total_profit or Decimal('0'),
            'avg_profit': row.avg_profit or Decimal('0'),
            'best_profit': row.best_profit,
            'worst_profit': row.worst_profit,
            'count': row.count or 0,
        }