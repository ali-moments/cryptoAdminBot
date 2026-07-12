from pathlib import Path
import sys

from loguru import logger

from app.config.settings import settings


def setup_logging() -> None:
    Path("logs").mkdir(exist_ok=True)

    logger.remove()

    logger.add(
        sys.stdout,
        level=settings.log_level,
        colorize=True,
        enqueue=True,
        backtrace=True,
        diagnose=False,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
    )

    logger.add(
        "logs/tradebot.log",
        level=settings.log_level,
        rotation="100 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
