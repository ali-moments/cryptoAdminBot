from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class RawTelegramMessage:
    channel_id: int

    message_id: int

    text: str

    date: datetime

    is_forwarded: bool

    forwarded_chat_id: int | None

    forwarded_message_id: int |None

    sender_id: int
