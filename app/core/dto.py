from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.database.enums import Direction


@dataclass(slots=True)
class ParsedEntry:
    number: int
    price: Decimal


@dataclass(slots=True)
class ParsedTarget:
    number: int
    price: Decimal


@dataclass(slots=True)
class ParsedSignal:
    source_id: int

    symbol: str

    direction: Direction

    leverage: int

    stop_loss: Decimal

    expires_at: datetime

    entries: list[ParsedEntry]

    targets: list[ParsedTarget]
