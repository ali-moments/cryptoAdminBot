from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.database.enums import SignalStatus, Direction
from app.database.models import Signal
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
