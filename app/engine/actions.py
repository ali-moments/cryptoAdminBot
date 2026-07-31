from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class EntryType(str, Enum):
    """Type of entry that occurred."""
    ENTRY_1 = "ENTRY_1"
    ENTRY_2 = "ENTRY_2"
    EMERGENCY_ENTRY = "EMERGENCY_ENTRY"


@dataclass(slots=True, frozen=True)
class PositionEntered:
    """
    Position entry action.
    
    Fields:
    - entry_type: How the position was entered (ENTRY_1, ENTRY_2, EMERGENCY_ENTRY)
    - price: The actual fill price
    - timestamp: When entry occurred
    """
    entry_type: EntryType
    price: Decimal
    timestamp: datetime


@dataclass(slots=True, frozen=True)
class WaitingEntryExpired:
    """
    Entry waiting period expired.
    
    This can happen when:
    - Signal expires after configured timeout
    - TP1 is crossed before any entry (signal opportunity missed)
    """
    reason: str  # "timeout" or "tp1_crossed"
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
