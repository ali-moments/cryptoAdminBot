from datetime import datetime, timedelta, UTC
from zoneinfo import ZoneInfo
import math
from decimal import Decimal
from typing import TYPE_CHECKING
import asyncio
from loguru import logger
from app.core.dto import ValidatedSignal
from app.database.enums import AuditEventType, Direction, SignalStatus, Provider, TrackingStatus
from app.database.models import (
    AuditLog,
    Signal,
    SignalEntry,
    SignalSource,
    SignalTarget,
    Tracking,
)
from app.database.uow import UnitOfWork
from app.services.settings import States
from app.telegram.common.dto import SignalDTO

if TYPE_CHECKING:
    from app.services.telegram import TelegramService


class SignalLifecycleService:
    def __init__(self, states: States, telegram_service: "TelegramService | None" = None) -> None:
        self.states = states
        self._telegram_service = telegram_service
        # In-memory cache to prevent duplicate Telegram messages from creating duplicate signals
        # Cache: key=(symbol, direction, first_entry, first_target) -> timestamp
        self._recent_signals: dict[tuple[str, Direction, Decimal, Decimal], datetime] = {}
        self._cache_lock = asyncio.Lock()
        self._cache_ttl_seconds = 15  # Keep entries for 10 seconds

    def _get_signal_cache_key(self, signal: ValidatedSignal) -> tuple[str, Direction, Decimal, Decimal]:
        """Create a cache key from signal characteristics."""
        first_entry = signal.entries[0].price if signal.entries else Decimal(0)
        first_target = signal.targets[0].price if signal.targets else Decimal(0)
        return (signal.symbol, signal.direction, first_entry, first_target)

    async def _is_recently_processed(self, signal: ValidatedSignal) -> bool:
        """
        Check if this signal was recently processed (within cache TTL).
        This prevents duplicate Telegram messages from creating duplicate signals.
        """
        async with self._cache_lock:
            # Clean up old entries
            now = datetime.now(UTC)
            expired_keys = [
                key for key, timestamp in self._recent_signals.items()
                if (now - timestamp).total_seconds() > self._cache_ttl_seconds
            ]
            for key in expired_keys:
                del self._recent_signals[key]

            # Check if signal was recently processed
            cache_key = self._get_signal_cache_key(signal)
            if cache_key in self._recent_signals:
                time_since = (now - self._recent_signals[cache_key]).total_seconds()
                logger.info(
                    f"Signal {signal.symbol} {signal.direction.value} ignored: "
                    f"already processed {time_since:.1f}s ago (likely duplicate Telegram message)"
                )
                return True

            # Mark as processed
            self._recent_signals[cache_key] = now
            return False

    @staticmethod
    def _is_in_quiet_hours() -> bool:
        """
        Check if current time falls in the quiet hours (22:00 to 5:20 Asia/Tehran).
        Signals received during this period should be ignored.

        Returns True if in quiet hours, False otherwise.
        """
        tehran_tz = ZoneInfo("Asia/Tehran")
        current_time = datetime.now(tehran_tz)
        current_hour = current_time.hour
        current_minute = current_time.minute

        # 22:00 to 23:59 (same day)
        if current_hour >= 22:
            return True

        # 00:00 to 5:20 (next day)
        if current_hour < 5:
            return True
        if current_hour == 5 and current_minute <= 20:
            return True

        return False

    @staticmethod
    def _normalize_leverage(n: int) -> int:
        """
        Normalize leverage according to the given rules:
        - ≤ 10          → 10
        - 11 … 19       → next even number (11→12, 13→14, …, 19→20)
        - ≥ 20          → ceiling to the next multiple of 5, capped at 40
        """
        if n <= 10:
            return 10

        if n <= 19:
            return n + (n % 2)  # make it even (round up when odd)

        # ceiling to nearest multiple of 5, then cap at 40
        return min(((n + 4) // 5) * 5, 40)

    @staticmethod
    def _calculate_leverage(
        entry: Decimal,
        stop_loss: Decimal,
        direction: Direction,
    ) -> int:
        """
        Calculate leverage based on entry price and stop loss.

        For LONG: uses entry_high (max entry)
        For SHORT: uses entry_low (min entry)

        Formula: leverage = normalize(ceil(80 / ((entry - sl) / entry * 100)))
        """
        # Calculate percentage distance from entry to stop loss
        distance_pct = abs((entry - stop_loss) / entry * Decimal(100))

        # Avoid division by zero
        if distance_pct == 0:
            return 10

        # Calculate raw leverage: 80 / distance_pct
        raw_leverage = Decimal(80) / distance_pct

        # Ceil and normalize
        leverage = math.ceil(raw_leverage)
        return SignalLifecycleService._normalize_leverage(leverage)

    async def _find_duplicate(
        self,
        signal: ValidatedSignal,
        uow: UnitOfWork,
    ) -> Signal | None:

        candidates = await uow.signals.find_active_candidates(
            symbol=signal.symbol,
            direction=signal.direction,
        )

        new_entries = {
            entry.price
            for entry in signal.entries
        }

        new_targets = {
            target.price
            for target in signal.targets
        }

        for candidate in candidates:
            db_entries = {
                entry.price
                for entry in candidate.entries
            }

            db_targets = {
                target.price
                for target in candidate.targets
            }

            entries_match = (
                new_entries <= db_entries
                or db_entries <= new_entries
            )

            targets_match = (
                new_targets <= db_targets
                or db_targets <= new_targets
            )

            if entries_match and targets_match:
                return candidate

        return None


    async def _should_cancel_signal(
        self,
        uow: UnitOfWork,
    ) -> bool:
        """
        Check if we should cancel the new signal due to capacity limit.

        Returns True if there are already {number} or more active trackings without any TP hits.
        """
        count = await uow.trackings.count_active_without_tp_hits()

        if count >= self.states.active_signals_limit:
            logger.info(
                f"Signal will be cancelled: {count} active trackings exist without TP hits (limit: {self.states.active_signals_limit})"
            )
            return True

        return False

    def _determine_provider(self, symbol: str) -> Provider:
        """Determine which exchange provider to use for this symbol.

        Logic:
        - Check symbol availability across exchanges
        - Use primary provider (e.g., Binance) by default
        - Fallback to secondary providers if needed
        """
        # Simple approach: default to Binance
        # You can make this more sophisticated based on:
        # - Symbol availability
        # - Exchange preferences
        return Provider.BINANCE

    def _create_signal_dto(self, signal: Signal) -> SignalDTO:
        """Convert database Signal model to SignalDTO for Telegram sending."""
        return SignalDTO(
            symbol=signal.symbol,
            direction=signal.direction.value,
            entries=[entry.price for entry in signal.entries],
            targets=[target.price for target in signal.targets],
            stop_loss=signal.stop_loss,
            leverage=signal.leverage,
        )


    async def create_signal(
        self,
        signal: ValidatedSignal,
        source: SignalSource,
        uow: UnitOfWork,
    ) -> Signal|None:
        # Check in-memory cache first (before any database operations)
        # This prevents duplicate Telegram messages from creating duplicate signals
        if await self._is_recently_processed(signal):
            return None

        # Check if we're in quiet hours (22:00 to 5:20 Asia/Tehran)
        if self._is_in_quiet_hours():
            logger.info(
                f"Signal for {signal.symbol} ignored: received during quiet hours (22:00-5:20 Asia/Tehran)"
            )
            # Create audit log for ignored signal
            await uow.audit_logs.add(
                AuditLog(
                    signal_id=None,
                    event=AuditEventType.SIGNAL_REJECTED,
                    payload={
                        "symbol": signal.symbol,
                        "direction": signal.direction.value,
                        "source_id": source.id,
                        "reason": "quiet_hours",
                        "entries": str(signal.entries),
                        "targets": str(signal.targets),
                        "stop_loss": str(signal.stop_loss),
                    }
                )
            )
            await uow.commit()
            return None

        duplicate = await self._find_duplicate(
            signal=signal,
            uow=uow,
        )

        if duplicate is not None:
            logger.trace("Duplicate signal ignored.")
            return duplicate

        # Check if we should cancel this signal due to capacity limit
        should_cancel = await self._should_cancel_signal(uow=uow)

        if should_cancel:
            logger.info(
                f"New signal for {signal.symbol} will be cancelled: "
                f"{self.states.active_signals_limit} or more active trackings without TP hits already exist."
            )


        # Calculate leverage based on entries and stop loss
        if signal.direction is Direction.LONG:
            # For LONG: use entry_high (max entry price)
            reference_entry = max(entry.price for entry in signal.entries)
        else:
            # For SHORT: use entry_low (min entry price)
            reference_entry = min(entry.price for entry in signal.entries)

        calculated_leverage = self._calculate_leverage(
            entry=reference_entry,
            stop_loss=signal.stop_loss,
            direction=signal.direction,
        )

        db_signal = Signal(
            source_id=source.id,
            symbol=signal.symbol,
            direction=signal.direction,
            leverage=calculated_leverage,
            stop_loss=signal.stop_loss,
            expires_at=datetime.now(UTC) + timedelta(hours=self.states.signal_expiry_timeout),
            status=SignalStatus.CANCELLED if should_cancel else SignalStatus.WAITING_ENTRY,
        )

        await uow.signals.add(db_signal)
        await uow.flush()

        logger.info("Signal added to db.")

        for entry in signal.entries:
            await uow.signal_entries.add(
                SignalEntry(
                    signal_id = db_signal.id,
                    position = entry.position,
                    price = entry.price,
                )
            )
        logger.trace("Entries added to db.")

        for target in signal.targets:
            await uow.signal_targets.add(
                SignalTarget(
                    signal_id = db_signal.id,
                    position = target.position,
                    price = target.price,
                )
            )
        logger.trace("Targets added to db.")

        await uow.audit_logs.add(
            AuditLog(
                signal_id=db_signal.id,
                event=AuditEventType.SIGNAL_REJECTED if should_cancel else AuditEventType.SIGNAL_RECEIVED,
                payload={
                    "symbol": db_signal.symbol,
                    "direction": db_signal.direction,
                    "source_id": db_signal.source_id,
                    "leverage": db_signal.leverage,
                    "stop_loss": str(db_signal.stop_loss),
                    "entries": str(signal.entries),
                    "targets": str(signal.targets),
                    "status": str(db_signal.status)
                }
            )
        )
        logger.info("Audit_log for the Signal saved to db.")

        # TODO: do not send the signal message in this scenario
        if not should_cancel:
            await uow.session.refresh(db_signal, ['targets'])
            tracking = Tracking(
                signal_id=db_signal.id,
                status=TrackingStatus.WAITING_ENTRY,
                provider=self._determine_provider(db_signal.symbol),
                is_active=True,
                started_at=datetime.now(UTC),
                current_stop_loss=db_signal.stop_loss,
                current_tp1_price=db_signal.targets[0].price if db_signal.targets else None,
            )
            db_tracking = await uow.trackings.add(tracking)
            await uow.flush()

            await uow.audit_logs.add(
                AuditLog(
                    signal_id=db_signal.id,
                    tracking_id=db_tracking.id,
                    event=AuditEventType.TRACKING_STARTED,
                    payload={
                        "provider": tracking.provider,
                        "symbol": db_signal.symbol,
                    }
                )
            )

            logger.info("Tracking created for signal.")

            # Send signal to Telegram channel
            if self._telegram_service:
                try:
                    await uow.session.refresh(db_signal, ['entries', 'targets'])
                    signal_dto = self._create_signal_dto(db_signal)
                    sent_message = await self._telegram_service.send_signal(db_tracking, signal_dto, uow)
                    if sent_message:
                        logger.info(f"Signal sent to Telegram for {db_signal.symbol}")
                    else:
                        logger.warning(f"Failed to send signal to Telegram for {db_signal.symbol}")
                except Exception as e:
                    logger.error(f"Error sending signal to Telegram: {e}")
            else:
                logger.warning("TelegramService not available, signal not sent to Telegram")

        return db_signal
