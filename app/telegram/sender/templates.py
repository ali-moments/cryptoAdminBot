import app.telegram.sender.emojies as emojies

class MessageTemplates:
    def __init__(self) -> None:
        pass

    SIGNAL = (
        f'<b>{emojies.SYMBOL} '+'{symbol} | {direction} {direction_arrow}</b>\n'
        f'<b>ENTRY{emojies.ENTRY}'+': {entry_text}</b>'
        f'<b>TP{emojies.TARGETS}'+': {target_lines}</b>'
        f'<b>SL{emojies.STOP_LOSS}'+': {stop_loss}</b>'
        f'<b>LV{emojies.LEVERAGE}'+': {leverage}X</b>\n'
        f'{emojies.TELEGRAM}'+' <b>@Royal_frx</b>'
    )

    TP_HIT = """"""

    ENTRY_HIT = """"""

    SL_HIT = """"""

    PNL = """"""

    GOOD_MORNING = """"""

    GOOD_NIGHT = """"""
