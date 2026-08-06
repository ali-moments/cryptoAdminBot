import os
from telethon import TelegramClient
from telethon.hints import DateLike
from app.config.settings import settings
from loguru import logger
from app.telegram.common.dto import SentMessage


class TelegramSender:
    def __init__(self,) -> None:
        self.client = TelegramClient(
            session=os.path.join(settings.sessions_dir, settings.sender_session),
            api_id=settings.sender_api_id,
            api_hash=settings.sender_api_hash,
            connection_retries=None,
            auto_reconnect=True,
            retry_delay=2,
            use_ipv6=False,
        )

    async def start(self) -> None:
        await self.client.start()
        logger.info("Sender session Started.")
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        await self.client.disconnect()
        logger.info("Sender session Stopped.")

    async def send_message(
        self,
        channel_id: int,
        message: str,
        reply_to: int|None = None,
        parse_mode: str = "html",
        link_preview: bool = False,
        schedule: DateLike|None=None,
    ) -> None|SentMessage:
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
        return None

    async def send_file(
        self,
        channel_id: int,
        message: str,
        file_path: str,
        reply_to: int|None = None,
        parse_mode: str = "html",
        link_preview: bool = False,
        schedule: DateLike|None=None,
    ) -> None|SentMessage:
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
        return None
