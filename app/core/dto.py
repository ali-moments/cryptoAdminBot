from dataclasses import dataclass
from decimal import Decimal

from app.database.enums import Direction


@dataclass(slots=True)
class ParsedEntry:
    position: int
    price: Decimal


@dataclass(slots=True)
class ParsedTarget:
    position: int
    price: Decimal


@dataclass(slots=True)
class ParsedSignal:
    symbol: str

    direction: Direction

    leverage: int

    stop_loss: Decimal

    entries: list[ParsedEntry]

    targets: list[ParsedTarget]


@dataclass(slots=True)
class ValidatedSignal:
    symbol: str

    direction: Direction

    leverage: int

    entries: list[ParsedEntry]

    targets: list[ParsedTarget]

    stop_loss: Decimal
