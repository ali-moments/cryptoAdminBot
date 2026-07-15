from dataclasses import dataclass

from app.telegram.common.dto import RawTelegramMessage


@dataclass(slots=True)
class TelegramMessageReceived:
    message: RawTelegramMessage
