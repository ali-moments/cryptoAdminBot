from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import selectinload

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
