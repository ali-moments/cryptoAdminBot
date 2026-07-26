from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(slots=True, frozen=True)
class PositionEntered:
    entry_number: int
    price: Decimal
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class WaitingEntryExpired:
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class StopLossHit:
    price: Decimal
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class TakeProfitHit:
    target_number: int
    price: Decimal
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class RiskFreed:
    price: Decimal
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class TrackingCompleted:
    timestamp: datetime
