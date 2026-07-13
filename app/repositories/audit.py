from sqlalchemy import select

from app.database.models import AuditLog
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditLog]):
    model = AuditLog

    async def by_signal(
        self,
        signal_id: int,
    ) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.signal_id == signal_id)
            .order_by(AuditLog.created_at)
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def by_tracking(
        self,
        tracking_id: int,
    ) -> list[AuditLog]:
        stmt = (
            select(AuditLog)
            .where(AuditLog.tracking_id == tracking_id)
            .order_by(AuditLog.created_at)
        )

        result = await self.session.scalars(stmt)
        return list(result)
