from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Integer,
    Numeric,
    String,
    DateTime,
    ForeignKey,
    SmallInteger,
    UniqueConstraint,
    Index,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Enum

from app.database.base import (
    Base, IDMixin, TimestampMixin, CreatedAtMixin, UUIDMixin
)
from app.database.enums import (
    Direction, SignalStatus, CloseReason, Provider, TrackingStatus, MessageType, AuditEventType
)

DIRECTION_ENUM = Enum(Direction, name="direction_enum")
SIGNAL_STATUS_ENUM = Enum(SignalStatus, name="signal_status_enum")
CLOSE_REASON_ENUM = Enum(CloseReason, name="close_reason_enum")
PROVIDER_ENUM = Enum(Provider, name="provider_enum")
TRACKING_STATUS_ENUM = Enum(TrackingStatus, name="tracking_status_enum")
MESSAGE_TYPE_ENUM = Enum(MessageType, name="message_type_enum")
AUDIT_EVENT_TYPE_ENUM = Enum(AuditEventType, name="audit_event_type_enum")


# ==============================================================================

class SignalSource(IDMixin, TimestampMixin, Base):
    __tablename__ = "signal_sources"

    name: Mapped[str] = mapped_column(
        String(100),
    )

    parser_name: Mapped[str] = mapped_column(
        String(100),
        index=True
    )

    telegram_channel_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
    )

    telegram_username: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        index=True,
    )

    # Manual priority used when two sources have the same score.
    manual_priority: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    # Score ×100 (875 = 8.75)
    score: Mapped[int] = mapped_column(
        Integer,
        default=0,
        index=True,
    )

    total_signals: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    winning_signals: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    losing_signals: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    cancelled_signals: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    expired_signals: Mapped[int] = mapped_column(
        Integer,
        default=0,
    )

    average_profit: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
        default=Decimal("0.0000"),
    )

    average_tp: Mapped[Decimal] = mapped_column(
        Numeric(6, 2),
        default=Decimal("0.00"),
    )

    best_profit: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
    )

    worst_profit: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
    )

    signals: Mapped[list["Signal"]] = relationship(
        back_populates="source",
    )

    @property
    def winrate(self) -> Decimal:
        total = self.winning_signals + self.losing_signals

        if total == 0:
            return Decimal("0.00")

        return (
            Decimal(self.winning_signals) * Decimal("100")
            / Decimal(total)
        )


# ============================================================================

class Signal(IDMixin, TimestampMixin, Base):
    __tablename__ = "signals"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("signal_sources.id", ondelete="RESTRICT"),
        index=True,
    )

    symbol: Mapped[str] = mapped_column(
        String(30),
        index=True,
    )

    direction: Mapped[Direction] = mapped_column(DIRECTION_ENUM)

    leverage: Mapped[int] = mapped_column(
        SmallInteger,
    )

    stop_loss: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    status: Mapped[SignalStatus] = mapped_column(
        SIGNAL_STATUS_ENUM,
        index=True,
    )

    source: Mapped["SignalSource"] = relationship(
        back_populates="signals",
    )

    entries: Mapped[list["SignalEntry"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        order_by="SignalEntry.position",
    )

    targets: Mapped[list["SignalTarget"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        order_by="SignalTarget.position",
    )

    tracking: Mapped["Tracking"] = relationship(
        back_populates="signal",
        uselist=False,
        cascade="all, delete-orphan",
    )


# ===================================================================

class SignalEntry(IDMixin, Base):
    __tablename__ = "signal_entries"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        index=True,
    )

    position: Mapped[int] = mapped_column(
        Integer,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
    )

    signal: Mapped["Signal"] = relationship(
        back_populates="entries",
    )

    __table_args__ = (
        UniqueConstraint(
            "signal_id",
            "position",
        ),
    )


# ===========================================================

class SignalTarget(IDMixin, Base):
    __tablename__ = "signal_targets"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        index=True,
    )

    position: Mapped[int] = mapped_column(
        Integer,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
    )

    signal: Mapped["Signal"] = relationship(
        back_populates="targets",
    )

    __table_args__ = (
        UniqueConstraint(
            "signal_id",
            "position",
        ),
    )


# ======================================================

class Tracking(IDMixin, TimestampMixin, Base):
    __tablename__ = "trackings"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    status: Mapped[TrackingStatus] = mapped_column(TRACKING_STATUS_ENUM)

    provider: Mapped[Provider] = mapped_column(PROVIDER_ENUM)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    last_processed_price: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8),
    )

    entry1_touched: Mapped[bool] = mapped_column(
        default=False,
    )

    entry2_touched: Mapped[bool] = mapped_column(
        default=False,
    )

    current_stop_loss: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
    )

    highest_target_hit: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
    )

    close_reason: Mapped[CloseReason | None] = mapped_column(CLOSE_REASON_ENUM)

    final_price: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8),
    )

    profit_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
    )

    # Execution state fields
    entry_price: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8),
    )

    peak_price_after_entry: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8),
    )

    halfway_to_tp1_reached: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    version: Mapped[int] = mapped_column(
        default=1,
    )

    signal: Mapped["Signal"] = relationship(
        back_populates="tracking",
    )

    @property
    def has_entered(self) -> bool:
        """Derived property - single source of truth from entry touched flags."""
        return self.entry1_touched or self.entry2_touched

    tp_hits: Mapped[list["TpHit"]] = relationship(
        back_populates="tracking",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_trackings_active",
            "id",
            postgresql_where=is_active.is_(True),
        ),
    )


# ==============================================================

class TpHit(IDMixin, CreatedAtMixin, Base):
    __tablename__ = "tp_hits"

    tracking_id: Mapped[int] = mapped_column(
        ForeignKey("trackings.id", ondelete="CASCADE"),
        index=True,
    )

    position: Mapped[int] = mapped_column(
        SmallInteger,
    )

    price: Mapped[Decimal] = mapped_column(
        Numeric(20, 8),
    )

    profit_percent: Mapped[Decimal] = mapped_column(
        Numeric(12, 4),
    )

    hit_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
    )

    tracking: Mapped["Tracking"] = relationship(
        back_populates="tp_hits",
    )

    __table_args__ = (
        UniqueConstraint(
            "tracking_id",
            "position",
        ),
    )


# ================================================================

class TelegramMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "telegram_messages"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        index=True,
    )

    tracking_id: Mapped[int | None] = mapped_column(
        ForeignKey("trackings.id", ondelete="CASCADE"),
        index=True,
    )

    type: Mapped[MessageType] = mapped_column(MESSAGE_TYPE_ENUM)

    channel_id: Mapped[int] = mapped_column(
        BigInteger,
    )

    message_id: Mapped[int] = mapped_column(
        BigInteger,
    )

    reply_to_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
    )

    signal: Mapped["Signal"] = relationship()

    tracking: Mapped["Tracking"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "message_id",
        ),
    )


# ======================================================================

class AuditLog(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"

    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"),
        index=True,
    )

    tracking_id: Mapped[int | None] = mapped_column(
        ForeignKey("trackings.id", ondelete="CASCADE"),
        index=True,
    )

    event: Mapped[AuditEventType] = mapped_column(AUDIT_EVENT_TYPE_ENUM)

    payload: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
    )

    signal: Mapped["Signal"] = relationship()

    tracking: Mapped["Tracking"] = relationship()
