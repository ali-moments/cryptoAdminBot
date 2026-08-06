from app.telegram.sender.client import TelegramSender
from app.telegram.sender.formatter import TelegramFormatter
from app.services.svg import SvgService

from app.database.models import Tracking
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

    async def send_signal(
        self,
        signal: SignalDTO
    ) -> SentMessage | None:
        """
        Sends signal data to channel.
        """
        text = self._formatter.format_signal(signal)
        return await self._sender.send_message(
            channel_id=self.states.target_channel,
            message=text,
        )

    async def send_sl_hit():
        pass

    async def send_entry_hit():
        pass

    async def send_tp_hit(
        self,
        tracking: Tracking,
        tp_number: int,
        profit_percent: float,
        reply_to: int,
    ) -> SentMessage | None:
        """
        Sends TP hit image with caption.
        """

        caption = self._formatter.format_tp_hit()

        #file_path = await self._svg.generate_profit_shot()
        file_path='generated/test.png'

        return await self._sender.send_file(
            message=caption,
            file_path=file_path,
            reply_to=reply_to,
        )

    async def send_pnl():
        pass

    async def send_good_morning():
        pass

    async def send_good_night():
        pass
