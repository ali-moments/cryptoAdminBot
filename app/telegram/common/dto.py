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


@dataclass(slots=True)
class PNLItem:
    """
    status:
        - TP{number}
            eg: TP1, TP2, ...
        - OPEN
        - STOP
    status_msg_id:
        - TP{number}: TP{number}'s telegram message id
        - STOP: STOP's telegram message id
        - OPEN: None
    pnl:
        pnl percentage, for 'open' status it should get and calculate that
        moment's profit or loss using market data(REST)
    """
    symbol: str
    signal_msg_id: int
    status: str
    status_msg_id: int | None
    pnl: Decimal


@dataclass(slots=True)
class PnlDTO:
    items: List[PNLItem]
    total: Decimal


@dataclass(slots=True)
class EntryHitDTO:
    symbol: str
    direction: str
    leverage: int
    entry_price: str
    entry_type: int  # 1 or 2
    datetime_str: str


@dataclass(slots=True)
class ProfitShotDTO:
    symbol: str
    direction: str
    leverage: int
    pnl: str
    entry_price: str
    exit_price: str
    datetime_str: str
