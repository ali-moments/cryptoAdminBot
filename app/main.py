import asyncio
import signal

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

    logger.info("Starting market data providers...")

    await app.market_manager.start()

    logger.info("Starting subscription synchronization...")

    await app.subscription_manager.start()

    logger.info("Starting tracking engine...")

    await app.tracking_manager.start()

    logger.info("Starting scheduler...")

    await app.scheduler.start()

    logger.info("starting Telegram sender module...")

    # Start the sender as a background task since it also runs indefinitely  
    # with await self.client.run_until_disconnected()
    sender_task = asyncio.create_task(app.sender._sender.start())

    logger.success("Application started successfully")

    # Setup graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler():
        logger.info("Received shutdown signal")
        shutdown_event.set()

    # Register signal handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Small delay to ensure signal handlers are registered
    await asyncio.sleep(0.1)

    logger.info("Starting Telegram reader module...")

    # Start the reader as a background task since it also runs indefinitely
    # with await self.client.run_until_disconnected()
    reader_task = asyncio.create_task(app.reader.start())

    try:
        # Wait for shutdown signal (both reader and sender run indefinitely)
        await shutdown_event.wait()
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt")
        shutdown_event.set()

    logger.info("Shutting down application...")

    # Shutdown in reverse order
    try:
        logger.info("Stopping Telegram sender...")
        # First disconnect the sender client gracefully
        await app.sender._sender.stop()
        # Then cancel the sender task
        if not sender_task.done():
            sender_task.cancel()
            try:
                await sender_task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        logger.error(f"Error stopping Telegram sender: {e}")

    try:
        logger.info("Stopping Telegram reader...")
        # First disconnect the client gracefully
        await app.reader.stop()
        # Then cancel the reader task
        if not reader_task.done():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
    except Exception as e:
        logger.error(f"Error stopping Telegram reader: {e}")

    try:
        logger.info("Stopping tracking engine...")
        await app.tracking_manager.stop()
    except Exception as e:
        logger.error(f"Error stopping tracking manager: {e}")

    try:
        logger.info("Stopping scheduler...")
        await app.scheduler.stop()
    except Exception as e:
        logger.error(f"Error stopping scheduler: {e}")

    try:
        logger.info("Stopping subscription synchronization...")
        await app.subscription_manager.stop()
    except Exception as e:
        logger.error(f"Error stopping subscription manager: {e}")

    try:
        logger.info("Stopping market data providers...")
        await app.market_manager.stop()
    except Exception as e:
        logger.error(f"Error stopping market manager: {e}")

    logger.info("Application shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
