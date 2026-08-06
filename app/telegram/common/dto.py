from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List


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


@dataclass(slots=True)
class SentMessage:
    id: int
    reply_to: int
    channel_id: int


@dataclass(slots=True)
class SignalDTO:
    symbol: str
    direction: str
    entries: List[Decimal]
    targets: List[Decimal]
    stop_loss: Decimal
    leverage: int

# todo:
# TpHitDTO
# StopLossDTO
# PNLDTO
# EntryHitDTO
