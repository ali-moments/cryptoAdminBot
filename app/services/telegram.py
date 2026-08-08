from typing import Any
from datetime import datetime
from zoneinfo import ZoneInfo

from app.telegram.sender.client import TelegramSender
from app.telegram.sender.formatter import TelegramFormatter
from app.services.svg import SvgService
from app.database.uow import UnitOfWork
from app.database.models import TpHit, Tracking
from app.database.enums import MessageType
from app.telegram.common.dto import SentMessage, SignalDTO, EntryHitDTO, ProfitShotDTO
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

    def _tehran_now_str(self) -> str:
        return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")


    async def send_signal(self, tracking: Tracking, signal: SignalDTO) -> SentMessage | None:
        """
        Sends signal data to channel.
        """
        text = self._formatter.format_signal(signal)
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
        )
        
        # Store signal message in database
        if sent_message:
            async with self._uow_factory() as uow:
                await uow.telegram_messages.create(
                    signal_id=tracking.signal_id,
                    tracking_id=tracking.id,
                    type=MessageType.SIGNAL,
                    channel_id=self.states.target_channel,
                    message_id=sent_message.id,
                    reply_to_message_id=None,
                )
                await uow.commit()
        
        return sent_message


    async def send_sl_hit(self, tracking: Tracking, loss_percent: str) -> SentMessage | None:
        """Send stop loss hit notification"""
        text = self._formatter.format_sl_hit(loss=loss_percent)
        
        # Get original signal message for reply-to
        reply_to_message_id = None
        async with self._uow_factory() as uow:
            signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
            if signal_message:
                reply_to_message_id = signal_message.message_id
        
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
        )
        
        # Store message in database
        if sent_message:
            async with self._uow_factory() as uow:
                await uow.telegram_messages.create(
                    signal_id=tracking.signal_id,
                    tracking_id=tracking.id,
                    type=MessageType.SIGNAL_CLOSED,
                    channel_id=self.states.target_channel,
                    message_id=sent_message.id,
                    reply_to_message_id=reply_to_message_id,
                )
                await uow.commit()
        
        return sent_message


    async def send_entry_hit(self, tracking: Tracking, entry_type: int, entry_price: str) -> SentMessage | None:
        """Send entry hit notification with image"""
        signal = tracking.signal
        
        # Format text message
        if entry_type == 2:
            text = self._formatter.format_second_entry_hit()
        else:
            text = self._formatter.format_first_entry_hit()

        # Generate entry shot image
        entry_dto = EntryHitDTO(
            symbol=signal.symbol,
            direction=signal.direction.value,
            leverage=signal.leverage,
            entry_price=entry_price,
            entry_type=entry_type,
            datetime_str=self._tehran_now_str()
        )
        
        file_path = self._svg.generate_entry_shot(
            pair=entry_dto.symbol,
            direction=entry_dto.direction,
            leverage=entry_dto.leverage,
            entry=entry_dto.entry_price,
            datetime_str=entry_dto.datetime_str,
        )

        # Get original signal message for reply-to
        reply_to_message_id = None
        async with self._uow_factory() as uow:
            signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
            if signal_message:
                reply_to_message_id = signal_message.message_id

        # Send file with caption
        sent_file = await self._sender.send_file(
            channel_id=self.states.target_channel,
            message=text,
            file_path=file_path,
            reply_to=reply_to_message_id,
        )
        
        # Clean up generated file
        self._svg.clear_shot_file(file_path)
        
        # Store message in database
        if sent_file:
            message_type = MessageType.ENTRY1_HIT if entry_type == 1 else MessageType.ENTRY2_HIT
            async with self._uow_factory() as uow:
                await uow.telegram_messages.create(
                    signal_id=tracking.signal_id,
                    tracking_id=tracking.id,
                    type=message_type,
                    channel_id=self.states.target_channel,
                    message_id=sent_file.id,
                    reply_to_message_id=reply_to_message_id,
                )
                await uow.commit()
        
        return sent_file


    async def send_tp_hit(self, tracking: Tracking, tp_hit: TpHit) -> SentMessage | None:
        """
        Sends TP hit image with caption.
        """
        signal = tracking.signal
        
        # Calculate leveraged profit percentage for display
        leveraged_profit = tp_hit.profit_percent * signal.leverage
        
        caption = self._formatter.format_tp_hit(tp_hit=tp_hit, leveraged_profit=leveraged_profit)

        # Generate profit shot image
        profit_dto = ProfitShotDTO(
            symbol=signal.symbol,
            direction=signal.direction.value,
            leverage=signal.leverage,
            pnl=f"{leveraged_profit:.2f}",
            entry_price=str(tracking.actual_entry_price),
            exit_price=str(tp_hit.price),
            datetime_str=self._tehran_now_str()
        )
        
        file_path = self._svg.generate_profit_shot(
            pair=profit_dto.symbol,
            direction=profit_dto.direction,
            leverage=profit_dto.leverage,
            pnl=profit_dto.pnl,
            entry=profit_dto.entry_price,
            exit_price=profit_dto.exit_price,
            datetime_str=profit_dto.datetime_str,
        )

        # Get original signal message for reply-to
        reply_to_message_id = None
        async with self._uow_factory() as uow:
            signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
            if signal_message:
                reply_to_message_id = signal_message.message_id

        # Send file with caption
        sent_file = await self._sender.send_file(
            channel_id=self.states.target_channel,
            message=caption,
            file_path=file_path,
            reply_to=reply_to_message_id,
        )
        
        # Clean up generated file
        self._svg.clear_shot_file(file_path)
        
        # Store message in database
        if sent_file:
            async with self._uow_factory() as uow:
                await uow.telegram_messages.create(
                    signal_id=tracking.signal_id,
                    tracking_id=tracking.id,
                    type=MessageType.TARGET_HIT,
                    channel_id=self.states.target_channel,
                    message_id=sent_file.id,
                    reply_to_message_id=reply_to_message_id,
                )
                await uow.commit()
        
        return sent_file


    async def send_signal_cancelled(self, tracking: Tracking, reason: str) -> SentMessage | None:
        """Send signal cancelled notification"""
        text = f"سیگنال {tracking.signal.symbol} لغو شد\nدلیل: {reason}"
        
        # Get original signal message for reply-to
        reply_to_message_id = None
        async with self._uow_factory() as uow:
            signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
            if signal_message:
                reply_to_message_id = signal_message.message_id
        
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
        )
        
        # Store message in database
        if sent_message:
            async with self._uow_factory() as uow:
                await uow.telegram_messages.create(
                    signal_id=tracking.signal_id,
                    tracking_id=tracking.id,
                    type=MessageType.SIGNAL_CANCELLED,
                    channel_id=self.states.target_channel,
                    message_id=sent_message.id,
                    reply_to_message_id=reply_to_message_id,
                )
                await uow.commit()
        
        return sent_message

    async def send_signal_closed(self, tracking: Tracking, reason: str) -> SentMessage | None:
        """Send signal closed notification for various reasons"""
        reason_text = {
            "risk_free": "ریسک فری",
            "all_targets_hit": "تمام اهداف",
            "expired": "منقضی شده"
        }.get(reason, reason)
        
        text = f"سیگنال {tracking.signal.symbol} بسته شد\nدلیل: {reason_text}"
        
        # Get original signal message for reply-to
        reply_to_message_id = None
        async with self._uow_factory() as uow:
            signal_message = await uow.telegram_messages.signal_message(tracking.signal_id)
            if signal_message:
                reply_to_message_id = signal_message.message_id
        
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
            reply_to=reply_to_message_id,
        )
        
        # Store message in database
        if sent_message:
            async with self._uow_factory() as uow:
                await uow.telegram_messages.create(
                    signal_id=tracking.signal_id,
                    tracking_id=tracking.id,
                    type=MessageType.SIGNAL_CLOSED,
                    channel_id=self.states.target_channel,
                    message_id=sent_message.id,
                    reply_to_message_id=reply_to_message_id,
                )
                await uow.commit()
        
        return sent_message

    async def send_pnl(self):
        # do not change this, i will fix that later
        pass


    async def send_good_morning(self) -> SentMessage | None:
        text = self._formatter.format_good_morning()
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
        )
        return sent_message


    async def send_good_night(self) -> SentMessage | None:
        text = self._formatter.format_good_night()
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
        )
        return sent_message
