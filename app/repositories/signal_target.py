from sqlalchemy import select

from app.database.models import SignalTarget
from app.repositories.base import BaseRepository


class SignalTargetRepository(BaseRepository[SignalTarget]):
    model = SignalTarget

    async def by_signal(
        self,
        signal_id: int,
    ) -> list[SignalTarget]:
        stmt = (
            select(SignalTarget)
            .where(SignalTarget.signal_id == signal_id)
            .order_by(SignalTarget.position)
        )

        result = await self.session.scalars(stmt)
        return list(result)
