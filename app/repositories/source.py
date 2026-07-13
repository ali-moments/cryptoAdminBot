from sqlalchemy import select

from app.database.models import SignalSource
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository[SignalSource]):
    model = SignalSource

    async def get_by_channel(
        self,
        telegram_channel_id: int,
    ) -> SignalSource | None:
        stmt = select(SignalSource).where(
            SignalSource.telegram_channel_id == telegram_channel_id,
        )

        return await self.session.scalar(stmt)

    async def active(self) -> list[SignalSource]:
        stmt = (
            select(SignalSource)
            .where(SignalSource.is_active.is_(True))
            .order_by(
                SignalSource.score.desc(),
                SignalSource.manual_priority.desc(),
            )
        )

        result = await self.session.scalars(stmt)
        return list(result)
