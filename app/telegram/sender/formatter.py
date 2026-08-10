from decimal import Decimal
from datetime import datetime
from random import choice
import app.telegram.sender.emojies as emojies
from app.telegram.sender.templates import MessageTemplates
from app.database.models import TpHit
from app.telegram.common.dto import (
    SignalDTO,
    PnlDTO
)

class TelegramFormatter:
    def __init__(self) -> None:
        self.templates = MessageTemplates()
        self.TARGET_ORDINALS = {
            1: "اول",
            2: "دوم",
            3: "سوم",
            4: "چهارم",
            5: "پنجم",
            6: "ششم",
            7: "هفتم",
            8: "هشتم",
            9: "نهم",
        }

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
            except:
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

    def _get_target_ordinal(self, target_position: int) -> str:
        return self.TARGET_ORDINALS.get(target_position, f'{target_position}ام')

    def _calculate_duration(self, start, end) -> str:
        """ gets time as input and returns duration string

        input:
            start: time
            end: time
            duration: timedelta = end - start
            return examples:
                1D, 3H, 45M
                2H, 30M
                54M
        """
        duration = end - start
        total_seconds = int(duration.total_seconds())

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days}D")
        if hours > 0:
            parts.append(f"{hours}H")
        if minutes > 0 or not parts:  # Show minutes if no other parts or if minutes > 0
            parts.append(f"{minutes}M")

        return ", ".join(parts)

    def _get_pnl_emoji(self, status: str) -> str:
        if status.startswith('TP'):
            return emojies.PNL_ITEM_TARGET
        if status == 'STOP':
            return emojies.PNL_ITEM_LOSS
        else:
            return emojies.PNL_ITEM_OPEN

    def format_tp_hit(self, tp_hit: TpHit, created_at, leveraged_profit: Decimal | None = None):
        # Use leveraged profit if provided, otherwise use the database profit_percent
        profit_to_display = leveraged_profit if leveraged_profit is not None else tp_hit.profit_percent

        text = ''.join(self.templates.TP_HIT.format(
            ordinal = self._get_target_ordinal(tp_hit.position),
            profit = self._normalize_number(profit_to_display),
            duration = self._calculate_duration(created_at, tp_hit.hit_at)
        ))
        if tp_hit.position < 5:
            text += ''.join(self.templates.TP_HIT_LIVE[tp_hit.position])
        elif tp_hit.position >= 5:
            text += choice(self.templates.TOP_TP)
        return text

    def format_first_entry_hit(self):
        return "first entry hit"

    def format_second_entry_hit(self, target) -> str:
        text = ''.join(self.templates.ENTRY_HIT.format(target=self._normalize_number(target)))
        return text

    def format_sl_hit(self, loss: str):
        text = ''.join(self.templates.SL_HIT.format(loss=loss))
        return text

    def format_signal(self, signal: SignalDTO):
        direction_arrow = emojies.LONG_ARROW if signal.direction == "LONG" else emojies.SHORT_ARROW

        if len(signal.entries) == 1:
            entry_text = self._normalize_number(signal.entries[0])
        else:
            entry_text = f"{self._normalize_number(signal.entries[0])} - {self._normalize_number(signal.entries[1])}"

        target_lines = " | ".join([self._normalize_number(x) for x in signal.targets])
        leverage = self._normalize_number(signal.leverage)
        symbol = signal.symbol.replace('USDT', '')

        test = "".join(self.templates.SIGNAL.format(
            symbol=symbol,
            direction=signal.direction,
            direction_arrow=direction_arrow,
            entry_text=entry_text,
            target_lines=target_lines,
            stop_loss=self._normalize_number(signal.stop_loss),
            leverage=leverage,
        ))
        return test

    def format_pnl(self, stats:PnlDTO):
        header = self.templates.PNL_HEADER

        pnl_items = []
        for item in stats.items:
            pnl_items.append(self.templates.PNL_ITEM.format(
                symbol=item.symbol,
                status=item.status,
                pnl=self._normalize_number(item.pnl),
                emoji=self._get_pnl_emoji(item.status)
            ))

        content = '\n'.join(pnl_items)
        footer = self.templates.PNL_FOOTER.format(pnl=self._normalize_number(stats.total))
        return header + content + footer

    def format_good_morning(self):
        day = datetime.now().day
        text = ''.join(self.templates.GM_TEXTS[day])
        text += self.templates.GM_FOOTER
        return text

    def format_good_night(self):
        text = ''.join(self.templates.GOOD_NIGHT)
        return text
