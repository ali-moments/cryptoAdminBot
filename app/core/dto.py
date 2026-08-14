from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime

from app.database.enums import Direction, Provider, TrackingStatus, CloseReason, EntryMethod


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


# === Statistics and Scoring DTOs ===

@dataclass
class MarketPrice:
    symbol: str
    price: Decimal
    timestamp: datetime
    provider: Provider


@dataclass
class TrackingDTO:
    tracking_id: int
    signal_id: int
    symbol: str
    direction: Direction
    status: TrackingStatus
    provider: Provider
    current_price: Decimal
    entry1_touched: bool
    entry2_touched: bool
    actual_entry_price: Decimal | None
    entry_method: EntryMethod | None
    current_tp1_price: Decimal | None
    current_stop_loss: Decimal
    highest_target_hit: int
    close_reason: CloseReason | None
    final_price: Decimal | None
    profit_percent: Decimal | None


@dataclass(frozen=True)
class SignalStatistics:
    """Statistics for a signal source over a time period."""
    source_id: int
    total_signals: int
    completed_signals: int
    active_signals: int
    tp_hit_count: int
    stop_loss_count: int
    cancelled_count: int
    expired_count: int
    tp_hit_rate: Decimal
    stop_loss_rate: Decimal
    total_profit: Decimal
    average_profit: Decimal
    best_profit: Decimal | None
    worst_profit: Decimal | None
    profitable_signal_count: int
    losing_signal_count: int


@dataclass(frozen=True)
class ScoreBreakdown:
    """Detailed breakdown of how a source score was calculated."""
    score: int  # 0-1000
    display_score: float  # 0.00-10.00
    tp_hit_rate_score: float  # 0.0-1.0
    profitability_score: float  # 0.0-1.0
    average_profit_score: float  # 0.0-1.0
    best_profit_score: float  # 0.0-1.0
    stop_loss_score: float  # 0.0-1.0 (1 - stop_loss_rate)
    confidence_score: float  # 0.0-1.0

    # Raw values for debugging
    tp_hit_rate: Decimal
    stop_loss_rate: Decimal
    total_profit: Decimal
    average_profit: Decimal
    best_profit: Decimal | None
    signal_count: int


@dataclass(frozen=True)
class TimeWindow:
    """Time window for statistics calculations."""
    name: str
    hours: int | None  # None for all-time

    @classmethod
    def all_time(cls) -> "TimeWindow":
        return cls("all-time", None)

    @classmethod
    def last_48h(cls) -> "TimeWindow":
        return cls("last-48h", 48)

    @classmethod
    def last_7d(cls) -> "TimeWindow":
        return cls("last-7d", 7 * 24)

    @classmethod
    def last_30d(cls) -> "TimeWindow":
        return cls("last-30d", 30 * 24)
