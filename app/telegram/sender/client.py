import os
import asyncio
from typing import TYPE_CHECKING
from telethon import TelegramClient
from telethon.hints import DateLike
from app.config.settings import settings
from loguru import logger
from app.telegram.common.dto import SentMessage
from app.database.enums import MessageType
from sqlalchemy import update

if TYPE_CHECKING:
    from app.database.uow import UnitOfWork


class TelegramSender:
    def __init__(self, uow_factory: type["UnitOfWork"]) -> None:
        self.client = TelegramClient(
            session=os.path.join(settings.sessions_dir, settings.sender_session),
            api_id=settings.sender_api_id,
            api_hash=settings.sender_api_hash,
            connection_retries=None,
            auto_reconnect=True,
            retry_delay=2,
            use_ipv6=False,
        )
        self._uow_factory = uow_factory
        self._queue_task = None
        self._running = False

    async def start(self) -> None:
        await self.client.start()
        logger.info("Sender session Started.")
        
        # Start queue processor
        self._running = True
        self._queue_task = asyncio.create_task(self._process_queue())
        
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        self._running = False
        if self._queue_task:
            self._queue_task.cancel()
            
        await self.client.disconnect()
        logger.info("Sender session Stopped.")

    async def queue_message(
        self,
        channel_id: int,
        message: str,
        reply_to: int | None = None,
        signal_id: int | None = None,
        tracking_id: int | None = None,
        telegram_message_type: str | None = None,
        uow: "UnitOfWork | None" = None,
    ) -> bool:
        """Queue a text message for sending"""
        try:
            if uow:
                # Use provided UnitOfWork (no commit - caller handles it)
                await uow.telegram_queue.create(
                    channel_id=channel_id,
                    message=message,
                    reply_to=reply_to,
                    file_path=None,
                    message_type="text",
                    signal_id=signal_id,
                    tracking_id=tracking_id,
                    telegram_message_type=telegram_message_type,
                )
                return True
            else:
                # Use separate UnitOfWork with commit
                async with self._uow_factory() as new_uow:
                    await new_uow.telegram_queue.create(
                        channel_id=channel_id,
                        message=message,
                        reply_to=reply_to,
                        file_path=None,
                        message_type="text",
                        signal_id=signal_id,
                        tracking_id=tracking_id,
                        telegram_message_type=telegram_message_type,
                    )
                    await new_uow.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to queue message: {e}")
            return False

    async def queue_file(
        self,
        channel_id: int,
        message: str,
        file_path: str,
        reply_to: int | None = None,
        signal_id: int | None = None,
        tracking_id: int | None = None,
        telegram_message_type: str | None = None,
        uow: "UnitOfWork | None" = None,
    ) -> bool:
        """Queue a file message for sending"""
        try:
            if uow:
                # Use provided UnitOfWork (no commit - caller handles it)
                await uow.telegram_queue.create(
                    channel_id=channel_id,
                    message=message,
                    reply_to=reply_to,
                    file_path=file_path,
                    message_type="file",
                    signal_id=signal_id,
                    tracking_id=tracking_id,
                    telegram_message_type=telegram_message_type,
                )
                return True
            else:
                # Use separate UnitOfWork with commit
                async with self._uow_factory() as new_uow:
                    await new_uow.telegram_queue.create(
                        channel_id=channel_id,
                        message=message,
                        reply_to=reply_to,
                        file_path=file_path,
                        message_type="file",
                        signal_id=signal_id,
                        tracking_id=tracking_id,
                        telegram_message_type=telegram_message_type,
                    )
                    await new_uow.commit()
                    return True
        except Exception as e:
            logger.error(f"Failed to queue file: {e}")
            return False

    async def _process_queue(self) -> None:
        """Background task to process message queue"""
        while self._running:
            try:
                async with self._uow_factory() as uow:
                    pending_messages = await uow.telegram_queue.get_pending_messages(limit=5)
                    
                    for queue_item in pending_messages:
                        await self._process_queue_item(queue_item, uow)
                        
            except Exception as e:
                logger.error(f"Queue processor error: {e}")
                
            await asyncio.sleep(1)  # Process every second

    async def _process_queue_item(self, queue_item, uow: "UnitOfWork") -> None:
        """Process individual queue item"""
        try:
            # Mark as processing
            await uow.telegram_queue.mark_processing(queue_item.id)
            await uow.commit()
            
            # Send message
            sent_message = None
            if queue_item.message_type == "text":
                sent_message = await self._send_message_direct(
                    channel_id=queue_item.channel_id,
                    message=queue_item.message,
                    reply_to=queue_item.reply_to,
                )
            elif queue_item.message_type == "file":
                sent_message = await self._send_file_direct(
                    channel_id=queue_item.channel_id,
                    message=queue_item.message,
                    file_path=queue_item.file_path,
                    reply_to=queue_item.reply_to,
                )
                
            if sent_message:
                # Clean up temporary files after successful sending
                if queue_item.message_type == "file" and queue_item.file_path:
                    try:
                        if os.path.exists(queue_item.file_path):
                            os.remove(queue_item.file_path)
                            logger.trace(f"Cleaned up temporary file: {queue_item.file_path}")
                    except Exception as e:
                        logger.warning(f"Failed to cleanup file {queue_item.file_path}: {e}")
                
                # Update telegram_messages table
                if queue_item.telegram_message_type and queue_item.signal_id:
                    # Convert string back to MessageType enum for telegram_messages table
                    message_type_enum = MessageType(queue_item.telegram_message_type)
                    await uow.telegram_messages.create(
                        signal_id=queue_item.signal_id,
                        tracking_id=queue_item.tracking_id,
                        type=message_type_enum,
                        channel_id=queue_item.channel_id,
                        message_id=sent_message.id,
                        reply_to_message_id=queue_item.reply_to,
                    )
                
                # Mark as completed
                await uow.telegram_queue.mark_completed(queue_item.id)
                await uow.commit()
                
                logger.trace(f"Message sent and recorded: {sent_message.id}")
            else:
                # Handle failure
                logger.warning(f"Failed to send message from queue item {queue_item.id}")
                
                # Clean up temporary files even on failure to avoid accumulating files
                if queue_item.message_type == "file" and queue_item.file_path:
                    try:
                        if os.path.exists(queue_item.file_path):
                            os.remove(queue_item.file_path)
                            logger.trace(f"Cleaned up temporary file after failure: {queue_item.file_path}")
                    except Exception as e:
                        logger.warning(f"Failed to cleanup file {queue_item.file_path}: {e}")
                
                if queue_item.retry_count < queue_item.max_retries:
                    # Reset to pending for retry (but without the file for file messages)
                    if queue_item.message_type == "file":
                        # For file messages, mark as failed since file is now gone
                        await uow.telegram_queue.mark_failed(queue_item.id)
                        logger.warning(f"File message {queue_item.id} marked as failed - file no longer exists for retry")
                    else:
                        # For text messages, can safely retry
                        await uow.telegram_queue.reset_for_retry(queue_item.id)
                else:
                    await uow.telegram_queue.mark_failed(queue_item.id)
                await uow.commit()
                
        except Exception as e:
            logger.error(f"Failed to process queue item {queue_item.id}: {e}")
            await uow.telegram_queue.mark_failed(queue_item.id)
            await uow.commit()

    async def _send_message_direct(
        self,
        channel_id: int,
        message: str,
        reply_to: int | None = None,
        parse_mode: str = "html",
        link_preview: bool = False,
        schedule: DateLike | None = None,
    ) -> SentMessage | None:
        """Direct message sending (internal use)"""
        try:
            sent_message = await self.client.send_message(
                entity=channel_id,
                message=message,
                reply_to=reply_to,
                parse_mode=parse_mode,
                link_preview=link_preview,
                schedule=schedule,
            )
            if sent_message:
                return SentMessage(sent_message.id, sent_message.reply_to_msg_id, sent_message.chat_id)
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
        return None

    async def _send_file_direct(
        self,
        channel_id: int,
        message: str,
        file_path: str,
        reply_to: int | None = None,
        parse_mode: str = "html",
        link_preview: bool = False,
        schedule: DateLike | None = None,
    ) -> SentMessage | None:
        """Direct file sending (internal use)"""
        try:
            sent_file = await self.client.send_file(
                entity=channel_id,
                file=file_path,
                caption=message,
                reply_to=reply_to,
                parse_mode=parse_mode,
                link_preview=link_preview,
                schedule=schedule,
            )
            if sent_file:
                return SentMessage(sent_file.id, sent_file.reply_to_msg_id, sent_file.chat_id)
        except Exception as e:
            logger.error(f"Failed to send file: {e}")
        return None

    # Keep original methods for backward compatibility if needed
    async def send_message(self, *args, **kwargs) -> SentMessage | None:
        """Deprecated: Use queue_message instead"""
        return await self._send_message_direct(*args, **kwargs)

    async def send_file(self, *args, **kwargs) -> SentMessage | None:
        """Deprecated: Use queue_file instead"""  
        return await self._send_file_direct(*args, **kwargs)
