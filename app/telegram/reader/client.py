from telethon import TelegramClient
import os
from app.config.settings import settings
from app.telegram.reader.manager import ReaderManager
from app.telegram.reader.handlers import register_handlers
from loguru import logger

class TelegramReader:
    def __init__(self, manager: ReaderManager,) -> None:
        self.manager = manager
        self.client = TelegramClient(
            session=os.path.join(settings.sessions_dir, settings.reader_session),
            api_id=settings.reader_api_id,
            api_hash=settings.reader_api_hash,
            connection_retries=None,
            auto_reconnect=True,
            retry_delay=2,
            use_ipv6=False,
        )
        register_handlers(self)

    async def start(self) -> None:
        await self.client.start()
        logger.info("Reader session Started.")
        await self.client.run_until_disconnected()

    async def stop(self) -> None:
        await self.client.disconnect()
        logger.info("Reader session Stopped.")
