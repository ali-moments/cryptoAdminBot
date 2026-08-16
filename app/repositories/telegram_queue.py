from app.repositories.base import BaseRepository
from app.database.models import TelegramMessageQueue
from sqlalchemy import select, update

class TelegramQueueRepository(BaseRepository[TelegramMessageQueue]):
    model = TelegramMessageQueue
    
    async def get_pending_messages(self, limit: int = 10) -> list[TelegramMessageQueue]:
        """Get pending messages ordered by creation time"""
        stmt = (
            select(TelegramMessageQueue)
            .where(TelegramMessageQueue.status == "pending")
            .order_by(TelegramMessageQueue.created_at)
            .limit(limit)
        )
        result = await self.session.scalars(stmt)
        return list(result)
    
    async def mark_processing(self, message_id: int) -> None:
        """Mark a message as being processed"""
        stmt = (
            update(TelegramMessageQueue)
            .where(TelegramMessageQueue.id == message_id)
            .values(status="processing")
        )
        await self.session.execute(stmt)
    
    async def mark_completed(self, message_id: int) -> None:
        """Mark a message as completed"""
        stmt = (
            update(TelegramMessageQueue)
            .where(TelegramMessageQueue.id == message_id)
            .values(status="completed")
        )
        await self.session.execute(stmt)
    
    async def mark_failed(self, message_id: int) -> None:
        """Mark a message as failed and increment retry count"""
        stmt = (
            update(TelegramMessageQueue)
            .where(TelegramMessageQueue.id == message_id)
            .values(
                status="failed",
                retry_count=TelegramMessageQueue.retry_count + 1
            )
        )
        await self.session.execute(stmt)
    
    async def reset_for_retry(self, message_id: int) -> None:
        """Reset message status to pending for retry"""
        stmt = (
            update(TelegramMessageQueue)
            .where(TelegramMessageQueue.id == message_id)
            .values(
                status="pending",
                retry_count=TelegramMessageQueue.retry_count + 1
            )
        )
        await self.session.execute(stmt)