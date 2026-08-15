from sqlalchemy import select

from app.database.enums import MessageType
from app.database.models import TelegramMessage
from app.repositories.base import BaseRepository


class TelegramRepository(BaseRepository[TelegramMessage]):
    model = TelegramMessage

    async def by_signal(
        self,
        signal_id: int,
    ) -> list[TelegramMessage]:
        stmt = (
            select(TelegramMessage)
            .where(TelegramMessage.signal_id == signal_id)
            .order_by(TelegramMessage.created_at)
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def by_tracking(
        self,
        tracking_id: int,
    ) -> list[TelegramMessage]:
        stmt = (
            select(TelegramMessage)
            .where(TelegramMessage.tracking_id == tracking_id)
            .order_by(TelegramMessage.created_at)
        )

        result = await self.session.scalars(stmt)
        return list(result)

    async def signal_message(
        self,
        signal_id: int,
    ) -> TelegramMessage | None:
        stmt = (
            select(TelegramMessage)
            .where(
                TelegramMessage.signal_id == signal_id,
                TelegramMessage.type == MessageType.SIGNAL,
            )
        )

        return await self.session.scalar(stmt)

    # === PNL-specific queries ===
    async def get_close_message(
        self,
        tracking_id: int,
    ) -> TelegramMessage | None:
        """Get SIGNAL_CLOSED message for tracking."""
        stmt = (
            select(TelegramMessage)
            .where(
                TelegramMessage.tracking_id == tracking_id,
                TelegramMessage.type == MessageType.SIGNAL_CLOSED,
            )
        )

        return await self.session.scalar(stmt)

    async def get_target_hit_messages(
        self,
        tracking_id: int,
    ) -> list[TelegramMessage]:
        """Get TARGET_HIT messages ordered by creation time for TP correlation."""
        stmt = (
            select(TelegramMessage)
            .where(
                TelegramMessage.tracking_id == tracking_id,
                TelegramMessage.type == MessageType.TARGET_HIT,
            )
            .order_by(TelegramMessage.created_at)
        )

        result = await self.session.scalars(stmt)
        return list(result)
