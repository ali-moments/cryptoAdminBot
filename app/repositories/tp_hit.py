from sqlalchemy import select

from app.database.models import TpHit
from app.repositories.base import BaseRepository


class TpHitRepository(BaseRepository[TpHit]):
    model = TpHit

    async def by_tracking(
        self,
        tracking_id: int,
    ) -> list[TpHit]:
        stmt = (
            select(TpHit)
            .where(TpHit.tracking_id == tracking_id)
            .order_by(TpHit.position)
        )

        result = await self.session.scalars(stmt)
        return list(result)
