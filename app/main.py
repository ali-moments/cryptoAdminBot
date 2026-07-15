import asyncio

from loguru import logger

from app.bootstrap import build_application
from app.config.logging import setup_logging


setup_logging()


async def main() -> None:
    logger.info("Building application...")

    app = build_application()

    logger.info("Refreshing Ourbit symbols...")

    await app.registry.refresh()

    logger.info(
        "Loaded {} Ourbit symbols.",
        app.registry.count,
    )

    logger.info("Starting Telegram reader...")

    await app.reader.start()


if __name__ == "__main__":
    asyncio.run(main())
