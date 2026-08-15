from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from datetime import datetime

from app.database.enums import TrackingStatus
from app.database.models import Tracking, Signal
from app.repositories.base import BaseRepository


class TrackingRepository(BaseRepository[Tracking]):
    model = Tracking

    async def get_full(
        self,
        tracking_id: int,
    ) -> Tracking | None:
        stmt = (
            select(Tracking)
            .where(Tracking.id == tracking_id)
            .options(
                selectinload(Tracking.signal).selectinload(Signal.entries),
                selectinload(Tracking.signal).selectinload(Signal.targets),
                selectinload(Tracking.signal).selectinload(Signal.source),
                selectinload(Tracking.tp_hits),
            )
        )

        return await self.session.scalar(stmt)

    async def get_active(self) -> list[Tracking]:
        stmt = (
            select(Tracking)
            .where(Tracking.is_active.is_(True))
            .options(
                selectinload(Tracking.signal).selectinload(Signal.entries),
                selectinload(Tracking.signal).selectinload(Signal.targets),
                selectinload(Tracking.signal).selectinload(Signal.source),
                selectinload(Tracking.tp_hits),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def get_by_signal(
        self,
        signal_id: int,
    ) -> Tracking | None:
        stmt = (
            select(Tracking)
            .where(Tracking.signal_id == signal_id)
        )

        return await self.session.scalar(stmt)

    async def get_by_status(
        self,
        status: TrackingStatus,
    ) -> list[Tracking]:
        stmt = select(Tracking).where(
            Tracking.status == status,
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def count_active_without_tp_hits_on_date(self, reference_date: datetime) -> int:
        """Count active trackings without TP hits created on the same UTC calendar day as reference_date."""
        from datetime import timedelta

        # Calculate UTC day boundaries
        start_of_day = reference_date.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        end_of_day = start_of_day + timedelta(days=1)

        # Join to Signal and filter by same UTC calendar day
        stmt = (
            select(Tracking)
            .join(Signal, Tracking.signal_id == Signal.id)
            .where(
                Tracking.is_active.is_(True),
                Tracking.highest_target_hit == 0,
                Signal.created_at >= start_of_day,
                Signal.created_at < end_of_day,
            )
        )
        result = await self.session.scalars(stmt)
        return len(list(result))


    # === Scoring-specific queries ===
    async def get_completed_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> list[Tracking]:
        """Get completed trackings for a specific source."""
        stmt = (
            select(Tracking)
            .join(Signal)
            .where(
                Signal.source_id == source_id,
                Tracking.status == TrackingStatus.CLOSED,
            )
            .options(
                selectinload(Tracking.signal),
                selectinload(Tracking.tp_hits),
            )
        )

        if since:
            stmt = stmt.where(Signal.created_at >= since)

        result = await self.session.scalars(stmt)
        return list(result)

    async def count_with_tp_hits_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> int:
        """Count trackings that have at least one TP hit."""
        from app.database.models import TpHit

        stmt = (
            select(func.count(func.distinct(Tracking.id)))
            .select_from(Tracking)
            .join(Signal)
            .join(TpHit)
            .where(
                Signal.source_id == source_id,
                Tracking.status == TrackingStatus.CLOSED,
            )
        )

        if since:
            stmt = stmt.where(Signal.created_at >= since)

        return (await self.session.scalar(stmt)) or 0

    async def get_profit_variance_by_source(
        self,
        source_id: int,
        since: datetime | None = None,
    ) -> dict:
        """
        Calculate profit variance and consistency metrics for a source.

        Returns statistics useful for consistency analysis.
        """
        trackings = await self.get_completed_by_source(source_id, since)

        profits = [
            float(t.profit_percent) for t in trackings
            if t.profit_percent is not None
        ]

        if not profits:
            return {
                "count": 0,
                "mean": 0.0,
                "variance": 0.0,
                "std_dev": 0.0,
                "coefficient_of_variation": 0.0,
            }

        import statistics

        mean = statistics.mean(profits)
        variance = statistics.variance(profits) if len(profits) > 1 else 0.0
        std_dev = statistics.stdev(profits) if len(profits) > 1 else 0.0
        cv = std_dev / abs(mean) if mean != 0 else 0.0

        return {
            "count": len(profits),
            "mean": mean,
            "variance": variance,
            "std_dev": std_dev,
            "coefficient_of_variation": cv,
        }

    # === PNL-specific queries ===
    async def get_closed_in_window(
        self,
        start: datetime,
        end: datetime,
    ) -> list[Tracking]:
        """Get closed trackings in time window with eager loading for PNL analytics."""
        stmt = (
            select(Tracking)
            .join(Signal)
            .where(
                Tracking.closed_at >= start,
                Tracking.closed_at < end,
                Tracking.status.in_([TrackingStatus.CLOSED, TrackingStatus.RISK_FREE]),
            )
            .options(
                selectinload(Tracking.signal).selectinload(Signal.source),
                selectinload(Tracking.signal).selectinload(Signal.entries),
                selectinload(Tracking.signal).selectinload(Signal.targets),
                selectinload(Tracking.tp_hits),
            )
            .order_by(Tracking.closed_at.desc())
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def get_by_signal_creation_window(
        self,
        start: datetime,
        end: datetime,
    ) -> list[Tracking]:
        """Get trackings whose signals were created in the time window."""
        stmt = (
            select(Tracking)
            .join(Signal)
            .where(
                Signal.created_at >= start,
                Signal.created_at < end,
            )
            .options(
                selectinload(Tracking.signal).selectinload(Signal.source),
                selectinload(Tracking.signal).selectinload(Signal.entries),
                selectinload(Tracking.signal).selectinload(Signal.targets),
                selectinload(Tracking.tp_hits),
            )
            .order_by(Signal.created_at.desc())
        )
        
        result = await self.session.scalars(stmt)
        return list(result)
