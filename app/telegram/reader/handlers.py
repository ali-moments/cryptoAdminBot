from telethon import events
from loguru import logger
from app.telegram.common.dto import RawTelegramMessage
from app.telegram.reader.events import TelegramMessageReceived


def register_handlers(reader) -> None:
    @reader.client.on(events.NewMessage)
    async def on_message(event):
        message = event.message
        logger.trace("telegram message received from {}, message:\n{}\n", event.chat_id, message.message)
        raw = RawTelegramMessage(
            channel_id=event.chat_id,
            message_id=message.id,
            text=message.raw_text or "",
            date=message.date,
            is_forwarded=message.forward is not None,
            forwarded_chat_id=(
                message.forward.chat_id
                if message.forward
                else None
            ),
            forwarded_message_id=(
                message.forward.channel_post
                if message.forward
                else None
            ),
        )

        if message.forward:
            logger.trace("a forwarded message found: {}", message.forward.chat_id)

        await reader.manager.dispatch(
            TelegramMessageReceived(raw)
        )
