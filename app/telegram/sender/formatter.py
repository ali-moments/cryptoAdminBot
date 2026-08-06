import app.telegram.sender.emojies as emojies
from app.telegram.sender.templates import MessageTemplates
from app.telegram.common.dto import (
    SignalDTO,
)

class TelegramFormatter:
    def __init__(self) -> None:
        self.templates = MessageTemplates()

    def format_tp_hit(self):
        return "target hit"

    def format_entry_hit(self):
        return "entry hit"

    def format_sl_hit(self):
        return "stopped"

    def format_signal(self, signal: SignalDTO):
        direction_arrow = emojies.LONG_ARROW2 if signal.direction == "LONG" else emojies.SHORT_ARROW2

        if len(signal.entries) == 1:
            entry_text = f"{signal.entries[0]}"
        else:
            entry_text = f"{signal.entries[0]} - {signal.entries[1]}"

        target_lines = " | ".join([str(x) for x in signal.targets])
        leverage = str(signal.leverage)
        symbol = signal.symbol.replace('USDT', '')

        test = "\n".join(self.templates.SIGNAL.format(
            symbol=symbol,
            direction=signal.direction,
            direction_arrow=direction_arrow,
            entry_text=entry_text,
            target_lines=target_lines,
            stop_loss=signal.stop_loss,
            leverage=leverage,
        ))
        return test

    def format_pnl(self):
        return "pnl message"

    def format_good_morning(self):
        return "good morning"

    def format_good_night(self):
        return "good night"
