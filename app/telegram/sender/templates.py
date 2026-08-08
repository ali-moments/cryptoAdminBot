import app.telegram.sender.emojies as emojies

class MessageTemplates:
    def __init__(self) -> None:
        pass

    SIGNAL = (
        f'<b>{emojies.PAIR} '+'{symbol} | {direction} {direction_arrow}</b>\n'
        f'<b>ورودی{emojies.ENTRY}'+': {entry_text}</b>'
        f'<b>حد سود{emojies.TARGETS}'+': {target_lines}</b>'
        f'<b>استاپ{emojies.STOP_LOSS}'+': {stop_loss}</b>'
        f'<b>لوریج{emojies.LEVERAGE}'+': {leverage}X</b>\n'
        f'{emojies.SIGNAL_TEXT}به لوریج داده شده دقت کنید مقدار ورودی برای هر سیگنال ۲٪ سرمایه هست.\n'
        f'<blockquote>{emojies.ROYAL}'+' <b>@Royal_frx</b></blockquote>'
    )

    TP_HIT = (
        '<b>تارگت '+'{ordinal}'+f' تاچ شد{emojies.TP_TEXT}</b>\n'
        f'<b>سود حاصل{emojies.TP_PROFIT}: '+'+{profit}%</b>\n'
        f'<b>مدت زمان{emojies.CLOCK}: '+'{duration}</b>\n'
        f'<b>مدیریت لایو{emojies.TP_FOOTER}:</b>\n'
    )

    ENTRY_HIT = """"""

    SL_HIT = (
        f'<b>استاپ تاچ شد{emojies.SL_HEADER}</b>\n'
        f'<b>ضرر{emojies.SL_PERCENTAGE}: '+'-{loss}%</b>\n'
        f'<b>جبران میشه عزیزان{emojies.SL_FOOTER1}{emojies.SL_FOOTER2}</b>'
    )

    PNL_HEADER = f"<b>{emojies.PNL_HEADER1}برآیند امروز تیم رویال{emojies.PNL_HEADER2}</b>\n\n"

    PNL_ITEM = "{symbol} {status} — {pnl} {emoji}\n"

    PNL_FOOTER = (
        f'{emojies.PNL_SPACER_FULL}\n'
        f'<b>{emojies.PNL_WINRATE} Win Rate: '+'{pnl}</b>\n'
        f'{emojies.PNL_SPACER_FULL}'
        f'{emojies.TELEGRAM} @Royal_frx'
    )

    GOOD_NIGHT = (
        f'<b>شبتون بخیر عزیزان{emojies.GN_HEADER}</b>\n'
        f'<b>کانال آموزشی{emojies.GN_TEXT}:</b>\n'
        f'<b>{emojies.GN_CHANNEL} @royaltrade_ac</b>\n'
        f'<b>{emojies.ROYAL} @Royal_frx | رویال</b>'
    )

    GOOD_MORNING = (
        f'<b>پیام صبح بخیر اینجا خواهد بود</b>\n'
        f'<b>{emojies.ROYAL} @Royal_frx | رویال</b>'
    )

    GM_TEXTS = {
        1: '',
        2: '',
    }
