from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database.enums import TrackingStatus
from app.database.models import Tracking
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
                selectinload(Tracking.signal),
                selectinload(Tracking.tp_hits),
            )
        )

        return await self.session.scalar(stmt)

    async def get_active(self) -> list[Tracking]:
        stmt = (
            select(Tracking)
            .where(Tracking.is_active.is_(True))
            .options(
                selectinload(Tracking.signal),
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
