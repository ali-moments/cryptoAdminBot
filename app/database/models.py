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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import (
    Base, IDMixin, TimestampMixin, CreatedAtMixin, UUIDMixin
)
from app.database.enums import (
    Direction, SignalStatus, CloseReason, Provider, TrackingStatus, MessageType, AuditEventType
)


# ==============================================================================

class SignalSource(IDMixin, TimestampMixin, Base):
    __tablename__ = "signal_sources"

    name: Mapped[str] = mapped_column(
        String(100),
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

    # telegram_channel_id: Mapped[int] = mapped_column(
    #     BigInteger,
    # )

    # telegram_message_id: Mapped[int] = mapped_column(
    #     BigInteger,
    #     unique=True,
    # )

    published_channel_id: Mapped[int | None] = mapped_column(
        BigInteger,
    )

    published_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
    )

    symbol: Mapped[str] = mapped_column(
        String(30),
        index=True,
    )

    direction: Mapped[Direction]

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
        index=True,
    )

    source: Mapped["SignalSource"] = relationship(
        back_populates="signals",
    )

    entries: Mapped[list["SignalEntry"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        order_by="SignalEntry.entry_number",
    )

    targets: Mapped[list["SignalTarget"]] = relationship(
        back_populates="signal",
        cascade="all, delete-orphan",
        order_by="SignalTarget.target_number",
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

    status: Mapped[TrackingStatus]

    provider: Mapped[Provider]

    is_active: Mapped[bool] = mapped_column(
        default=True,
        index=True,
    )

    started_at: Mapped[datetime | None]

    closed_at: Mapped[datetime | None]

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

    close_reason: Mapped[CloseReason | None]

    final_price: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 8),
    )

    profit_percent: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4),
    )

    version: Mapped[int] = mapped_column(
        default=1,
    )

    signal: Mapped["Signal"] = relationship(
        back_populates="tracking",
    )

    tp_hits: Mapped[list["TpHit"]] = relationship(
        back_populates="tracking",
        cascade="all, delete-orphan",
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

    hit_at: Mapped[datetime]

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

    type: Mapped[MessageType]

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

    event: Mapped[AuditEventType]

    payload: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
    )

    signal: Mapped["Signal"] = relationship()

    tracking: Mapped["Tracking"] = relationship()
