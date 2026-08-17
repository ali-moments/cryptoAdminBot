from datetime import datetime
from zoneinfo import ZoneInfo
from decimal import Decimal
from loguru import logger
from app.telegram.sender.client import TelegramSender
from app.telegram.sender.formatter import TelegramFormatter
from app.services.svg import SvgService
from app.database.uow import UnitOfWork
from app.database.models import Signal, TpHit, Tracking
from app.database.enums import MessageType, EntryMethod, Direction
from app.telegram.common.dto import PnlDTO, SignalDTO, ProfitShotDTO
from app.services.settings import States


class TelegramService:
    def __init__(
        self,
        sender: TelegramSender,
        formatter: TelegramFormatter,
        svg: SvgService,
        states: States,
        uow_factory: type[UnitOfWork]
    ) -> None:
        self._sender = sender
        self._formatter = formatter
        self._svg = svg
        self.states = states
        self._uow_factory = uow_factory
        self.profit_shot_counter = 0

    def _check_shot_counter(self) -> bool:
        if self.profit_shot_counter == 4:
            self.profit_shot_counter = 0
            return True
        else:
            self.profit_shot_counter += 1
            return False

    def _tehran_now_str(self) -> str:
        return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")

    def _normalize_number(self, value: Decimal | float | str) -> str:
        """
        Normalize number by removing unnecessary trailing zeros.
        Examples:
        23.63640000000 -> 23.6364
        10.00 -> 10
        0.50 -> 0.5
        """
        if isinstance(value, str):
            try:
                value = Decimal(value)
            except Exception:
                return value

        # Convert to Decimal if it's a float
        if isinstance(value, float):
            value = Decimal(str(value))

        # Format as string and remove trailing zeros
        formatted = f"{value:.10f}".rstrip('0').rstrip('.')

        # Handle edge case where we might end up with empty string
        if not formatted or formatted == '-':
            return "0"

        return formatted


    def _get_effective_entry_price(self, tracking: Tracking) -> Decimal | None:
        """
        Calculate the effective entry price for profit/loss calculations.

        If both entry1 and entry2 are touched, returns the average: (entry1 + entry2) / 2
        If emergency entry and entry1 are both touched, returns average of emergency and entry1
        Otherwise, returns the actual_entry_price.
        """
        if not tracking.actual_entry_price:
            return None

        signal_entries = tracking.signal.entries

        # If both entries are touched, calculate average entry
        if tracking.entry1_touched and tracking.entry2_touched:
            if len(signal_entries) >= 2:
                # Get both entry prices
                entry1_price = signal_entries[0].price
                entry2_price = signal_entries[1].price
                # Return average
                avg_entry = (entry1_price + entry2_price) / Decimal("2")
                return avg_entry.quantize(Decimal("0.00000001"))
        elif tracking.entry1_touched and tracking.entry_method == EntryMethod.EMERGENCY_ENTRY:
            # Both emergency and entry1 touched - average them
            emergency_price = self._calculate_emergency_entry_price_for_audit(tracking.signal)
            if emergency_price and len(signal_entries) >= 1:
                entry1_price = signal_entries[0].price
                # Return average of emergency entry and entry1
                avg_entry = (emergency_price + entry1_price) / Decimal("2")
                return avg_entry.quantize(Decimal("0.00000001"))

        # Otherwise, use the actual entry price
        return tracking.actual_entry_price

    def _calculate_emergency_entry_price_for_audit(
        self,
        signal: Signal,
    ) -> Decimal | None:
        """
        Calculate emergency entry price for audit logging purposes.

        This duplicates the logic from EntryRule for diagnostic purposes.
        The actual business logic uses EntryRule's calculation.

        Formula:
        - LONG: emergency = EntryHigh + (TP1 - EntryHigh) / 5
        - SHORT: emergency = EntryLow - (EntryLow - TP1) / 5

        For LONG: EntryHigh is the higher price, emergency is between EntryHigh and TP1
        For SHORT: EntryLow is the lower price, emergency is between EntryLow and TP1
        """
        if not signal.entries or not signal.targets:
            return None

        sorted_entries = sorted(signal.entries, key=lambda e: e.price)
        tp1_price = signal.targets[0].price
        direction = signal.direction

        if direction == Direction.LONG:
            # LONG: EntryHigh is the higher price
            entry_high_price = sorted_entries[-1].price
            distance = tp1_price - entry_high_price
            fifth_distance = distance / Decimal("5")
            # Emergency entry is EntryHigh + fifth distance toward TP1
            return entry_high_price + fifth_distance
        else:
            # SHORT: EntryLow is the lower price
            entry_low_price = sorted_entries[0].price
            distance = entry_low_price - tp1_price
            fifth_distance = distance / Decimal("5")
            # Emergency entry is EntryLow - fifth distance toward TP1
            return entry_low_price - fifth_distance


    async def send_signal(self, tracking: Tracking, signal: SignalDTO, uow: UnitOfWork) -> bool:
        """
        Queue signal message for sending
        """
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping signal send")
            return False

        text = self._formatter.format_signal(signal)
        
        # Queue message instead of sending directly
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            signal_id=tracking.signal_id,
            tracking_id=tracking.id,
            telegram_message_type=MessageType.SIGNAL.value,
            uow=uow,
        )
        
        if success:
            logger.trace(f"Signal queued for {tracking.signal.symbol}")
        else:
            logger.warning(f"Failed to queue signal for {tracking.signal.symbol}")
            
        return success


    async def send_sl_hit(self, tracking: Tracking, loss_percent: str, uow: UnitOfWork) -> bool:
        """Queue stop loss hit notification"""
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping SL hit send")
            return False

        text = self._formatter.format_sl_hit(loss=loss_percent)

        # Get original signal message for reply-to
        reply_to_message_id = None
        signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
        if signal_message:
            reply_to_message_id = signal_message.message_id

        # Queue message
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
            signal_id=tracking.signal_id,
            tracking_id=tracking.id,
            telegram_message_type=MessageType.SIGNAL_CLOSED.value,
            uow=uow,
        )

        return success


    async def send_entry_hit(self, tracking: Tracking, entry_type: int, entry_price: str, uow: UnitOfWork, target) -> bool:
        """Queue entry hit notification"""
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping entry hit send")
            return False

        # Format message
        avg_entry = self._get_effective_entry_price(tracking=tracking)
        if entry_type == 2:
            text = self._formatter.format_second_entry_hit(target=target, entry=avg_entry)
        else:
            text = self._formatter.format_first_entry_hit()

        # Get reply-to message
        reply_to_message_id = None
        signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
        if signal_message:
            reply_to_message_id = signal_message.message_id

        # Queue message
        message_type = MessageType.ENTRY1_HIT.value if entry_type == 1 else MessageType.ENTRY2_HIT.value
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
            signal_id=tracking.signal_id,
            tracking_id=tracking.id,
            telegram_message_type=message_type,
            uow=uow,
        )

        return success


    async def send_tp_hit(self, tracking: Tracking, tp_hit: TpHit, uow: UnitOfWork) -> bool:
        """
        Queue TP hit message with image
        """
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping TP hit send")
            return False

        signal = tracking.signal

        # Calculate leveraged profit percentage for display
        leveraged_profit = tp_hit.profit_percent * signal.leverage

        caption = self._formatter.format_tp_hit(tp_hit=tp_hit, created_at=tracking.created_at, leveraged_profit=leveraged_profit)

        # Calculate effective entry price (average if both entries touched)
        effective_entry_price = self._get_effective_entry_price(tracking=tracking)

        # Generate profit shot image
        profit_dto = ProfitShotDTO(
            symbol=signal.symbol,
            direction=signal.direction.value,
            leverage=signal.leverage,
            pnl=f"{leveraged_profit:.2f}",
            entry_price=str(effective_entry_price),
            exit_price=str(tp_hit.price),
            datetime_str=self._tehran_now_str()
        )

        file_path = self._svg.generate_profit_shot(
            pair=profit_dto.symbol,
            direction=profit_dto.direction,
            leverage=profit_dto.leverage,
            pnl=profit_dto.pnl,
            entry=self._normalize_number(profit_dto.entry_price),
            exit_price=self._normalize_number(profit_dto.exit_price),
            datetime_str=profit_dto.datetime_str,
        )

        # Get original signal message for reply-to
        reply_to_message_id = None
        signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
        if signal_message:
            reply_to_message_id = signal_message.message_id

        # Queue file
        success = await self._sender.queue_file(
            channel_id=self.states.target_channel,
            message=caption,
            file_path=file_path,
            reply_to=reply_to_message_id,
            signal_id=tracking.signal_id,
            tracking_id=tracking.id,
            telegram_message_type=MessageType.TARGET_HIT.value,
            uow=uow,
        )

        # Don't clean up file here - let queue processor handle it after sending

        # Queue profit shot message if counter triggers
        if self._check_shot_counter():
            await self._sender.queue_message(
                channel_id=self.states.target_channel,
                message=self._formatter.format_profit_shot(),
                uow=uow,
            )

        return success


    async def send_signal_cancelled(self, tracking: Tracking, reason: str, uow: UnitOfWork) -> bool:
        """Queue signal cancelled notification"""
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping signal cancelled send")
            return False

        text = f"سیگنال {tracking.signal.symbol} لغو شد\nدلیل: {reason}"

        # Get original signal message for reply-to
        reply_to_message_id = None
        signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
        if signal_message:
            reply_to_message_id = signal_message.message_id

        # Queue message
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
            signal_id=tracking.signal_id,
            tracking_id=tracking.id,
            telegram_message_type=MessageType.SIGNAL_CANCELLED.value,
            uow=uow,
        )

        return success

    async def send_signal_closed(self, tracking: Tracking, reason: str, uow: UnitOfWork) -> bool:
        """Queue signal closed notification for various reasons"""
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping signal closed send")
            return False

        reason_text = {
            "risk_free": "ریسک فری",
            "all_targets_hit": "تمام اهداف",
            "expired": "منقضی شده",
            "admin_stop": "توقف توسط ادمین"  # New admin stop reason
        }.get(reason, reason)

        text = f"سیگنال {tracking.signal.symbol} بسته شد\nدلیل: {reason_text}"

        # Get original signal message for reply-to
        reply_to_message_id = None
        signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
        if signal_message:
            reply_to_message_id = signal_message.message_id

        # Queue message
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
            signal_id=tracking.signal_id,
            tracking_id=tracking.id,
            telegram_message_type=MessageType.SIGNAL_CLOSED.value,
            uow=uow,
        )

        return success

    async def send_admin_stop_message(self, tracking: Tracking, uow: UnitOfWork) -> bool:
        """Send admin stop notification"""
        return await self.send_signal_closed(tracking, "admin_stop", uow)

    async def send_admin_entry_hit(self, tracking: Tracking, entry_position: int, uow: UnitOfWork) -> bool:
        """Send admin-triggered entry hit notification"""
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping admin entry hit send")
            return False

        # Get the entry price
        entries = tracking.signal.entries
        if entry_position < 1 or entry_position > len(entries):
            logger.error(f"Invalid entry position {entry_position} for tracking {tracking.id}")
            return False

        entry_price = str(entries[entry_position - 1].price)
        
        # Calculate target for second entry if needed
        target = None
        if entry_position == 2:
            # For second entry, calculate new TP1 if needed
            avg_entry = self._get_effective_entry_price(tracking=tracking)
            if avg_entry and tracking.signal.targets:
                target = avg_entry  # Simplified - use average entry as target reference

        # Use existing entry hit method with admin context
        return await self.send_entry_hit(tracking, entry_position, entry_price, uow, target)

    async def send_admin_tp_hit(self, tracking: Tracking, tp_position: int, uow: UnitOfWork) -> bool:
        """Send admin-triggered TP hit notification"""
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping admin TP hit send")
            return False

        # Get the TP hit record from the same UnitOfWork
        tp_hit = await uow.tp_hits.get_by_tracking_and_position(tracking.id, tp_position)
        
        if not tp_hit:
            logger.error(f"TP hit record not found for tracking {tracking.id}, position {tp_position}")
            return False

        # Use existing TP hit method
        return await self.send_tp_hit(tracking, tp_hit, uow)

    async def send_pnl(self, pnldto: PnlDTO) -> bool:
        """
        Queue telegram pnl message
        """
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping PnL send")
            return False

        reply_to = None
        # top_tp = 0
        # for item in pnldto.items:
        #     if item.status.startswith('TP'):
        #         try:
        #             if int(item.status.replace('TP', '')) > top_tp:
        #                 top_tp = int(item.status.replace('TP', ''))
        #                 reply_to = item.status_msg_id
        #         except Exception:
        #             continue

        text = self._formatter.format_pnl(pnldto, self.states.target_channel)
        
        # Queue message
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to,
        )
        return success


    async def send_good_morning(self) -> bool:
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping good morning send")
            return False

        text = self._formatter.format_good_morning()
        
        # Queue message
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            telegram_message_type=MessageType.DAILY_REPORT.value,
        )
        return success


    async def send_good_night(self) -> bool:
        if not self.states.bot_enabled:
            logger.trace("Bot disabled, skipping good night send")
            return False

        text = self._formatter.format_good_night()
        
        # Queue message
        success = await self._sender.queue_message(
            channel_id=self.states.target_channel,
            message=text,
            telegram_message_type=MessageType.DAILY_REPORT.value,
        )
        return success
