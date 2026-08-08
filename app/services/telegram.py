from typing import Any

from app.telegram.sender.client import TelegramSender
from app.telegram.sender.formatter import TelegramFormatter
from app.services.svg import SvgService

from app.database.models import TpHit, Tracking
from app.telegram.common.dto import SentMessage, SignalDTO
from app.services.settings import States


class TelegramService:
    def __init__(
        self,
        sender: TelegramSender,
        formatter: TelegramFormatter,
        svg: SvgService,
        states: States
    ) -> None:
        self._sender = sender
        self._formatter = formatter
        self._svg = svg
        self.states = states


    async def send_signal(self, tracking: Tracking, signal: SignalDTO) -> SentMessage | None:
        """
        Sends signal data to channel.
        """
        text = self._formatter.format_signal(signal)
        sent_message = await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
        )
        # todo: add it to telegram messages db ('SIGNAL')
        return sent_message


    async def send_sl_hit(self, tracking: Tracking, loss: Any):
        text = self._formatter.format_sl_hit(loss=loss)
        sent_message =  await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
        )
        # todo: add it to telegram messages db ('SIGNAL_CLOSED')
        return sent_message


    async def send_entry_hit(self, tracking: Tracking, position: int) -> SentMessage | None:
        text = ''
        if position == 2:
            text = self._formatter.format_second_entry_hit()
        else:
            text = self._formatter.format_first_entry_hit()

        # todo: get all needed data from db and generate_entry_shot
        #file_path = self._svg.generate_entry_shot()
        file_path='generated/test.png'

        # todo: get relevent signal's message id, the entry hit message should replied to its signal
        # find the message id from db
        reply_to = ''

        sent_file =  await self._sender.send_file(
            self.states.target_channel,
            message=text,
            file_path=file_path,
            reply_to=reply_to,
        )
        # todo: add it to telegram messages db 'ENTRY1_HIT' 'ENTRY2_HIT'
        return sent_file


    async def send_tp_hit(self, tracking: Tracking, tp_hit: TpHit) -> SentMessage | None:
        """
        Sends TP hit image with caption.
        """

        caption = self._formatter.format_tp_hit(tp_hit=tp_hit)

        # todo: get all needed data from db and generate_profit_shot
        #file_path = self._svg.generate_profit_shot()
        file_path='generated/test.png'

        # todo: get relevent signal's message id, the tphit message should replied to its signal
        # find the message id from db
        reply_to = ''

        sent_file = await self._sender.send_file(
            channel_id=self.states.target_channel,
            message=caption,
            file_path=file_path,
            reply_to=reply_to,
        )
        # todo: add it to telegram messages db 'TARGET_HIT'
        return sent_file


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
