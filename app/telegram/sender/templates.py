import app.telegram.sender.emojies as emojies

class MessageTemplates:
    def __init__(self) -> None:
        pass

    SIGNAL = (
        f'<b>{emojies.PAIR} '+'{symbol} | {direction} {direction_arrow}</b>\n\n'
        f'<b>ورودی{emojies.ENTRY}'+': {entry_text}</b>\n'
        f'<b>حد سود{emojies.TARGETS}'+': {target_lines}</b>\n'
        f'<b>استاپ{emojies.STOP_LOSS}'+': {stop_loss}</b>\n'
        f'<b>لوریج{emojies.LEVERAGE}'+': {leverage}X</b>\n\n'
        f'{emojies.SIGNAL_TEXT}به لوریج داده شده دقت کنید مقدار ورودی برای هر سیگنال ۲٪ سرمایه هست.\n\n'
        f'<blockquote>{emojies.ROYAL}'+' <b>@Royal_frx</b></blockquote>'
    )

    TP_HIT = (
        '<b>تارگت '+'{ordinal}'+f' تاچ شد{emojies.TP_TEXT}</b>\n\n'
        f'<b>سود حاصل{emojies.TP_PROFIT}: '+'+{profit}%</b>\n\n'
        f'<b>مدت زمان{emojies.CLOCK}: '+'{duration}</b>\n\n'
        f'<b>مدیریت لایو{emojies.TP_FOOTER}:</b>\n'
    )

    TP_HIT_LIVE = {
        1: (
            f'<b>●40٪ پوزیشن رو سیو کنید{emojies.TP_LIVE1}</b>\n'
            f'<b>●استاپ رو بین ورود و استاپ بذارید که معامله ریسک فری بشه{emojies.TP_LIVE2}</b>\n'
        ),
        2: (
            f'<b>●25٪ پوزیشن رو سیو کنید{emojies.TP_LIVE1}</b>\n'
            f'<b>●استاپ رو بذارید زیر ورود{emojies.TP_LIVE2}</b>\n'
        ),
        3: (
            f'<b>●25٪ پوزیشن رو سیو کنید{emojies.TP_LIVE1}</b>\n'
            f'<b>●استاپ رو بذارید زیر تارگت اول{emojies.TP_LIVE2}</b>\n'
        ),
        4: (
            f'<b>●میتونید کامل ببندید{emojies.TP_LIVE1}</b>\n'
            f'<b>●استاپ بیاد روی تارگت اول{emojies.TP_LIVE2}</b>\n'
        ),
    }

    TOP_TP = [
        f'<b>به به نوش جانتون{emojies.TOPTP1}</b>',
        f'<b>حیف میطلبیم{emojies.TOPTP2}</b>',
        f'<b>رو دست ندارم داشم{emojies.TOPTP3}</b>',
        f'<b>کل وال‌استریت‌ ندید چند ؟{emojies.TOPTP4}</b>',
        f'<b>خیلی خوبم من{emojies.TOPTP5}</b>',
        f'<b>اژدها سوارم داشت تارگارینه{emojies.TOPTP6}</b>',
        f'<b>زیبا و تمیز از نظر پسرا{emojies.TOPTP7}</b>',
        f'<b>من خیلی خوبم مگه نه پسرا؟{emojies.TOPTP8}</b>',
        f'<b>برادرا همه سیو سود کنید{emojies.TOPTP9}</b>',
        f'<b>حریف میطلبم{emojies.TOPTP10}</b>',
        f'<b>به راستی که حریف میطلبیم{emojies.TOPTP11}</b>',
        f'<b>نوش جون همه بجز پزشکیان{emojies.TOPTP12}</b>',
        f'<b>نوش جان همگی ، من همینقدر خوبم{emojies.TOPTP13}</b>',
        f'<b>شماره یکم تو کارم{emojies.TOPTP14}</b>',
    ]

    ENTRY_HIT = (
        f'<b>ورود دوم فعال شد{emojies.ENTRY_HIT1}</b>\n\n'
        f'<b>میانگین{emojies.ENTRY_HIT2}: '+'{entry}</b>\n'
        f'<b>تارگت جدید{emojies.ENTRY_HIT3}: '+'{target}</b>\n\n'
        f'{emojies.TELEGRAM} @Royal_frx | رویال'
    )

    SL_HIT = (
        f'<b>استاپ تاچ شد{emojies.SL_HEADER}</b>\n\n'
        f'<b>ضرر{emojies.SL_PERCENTAGE}: '+'-{loss}%</b>\n\n'
        f'<b>جبران میشه عزیزان{emojies.SL_FOOTER1}{emojies.SL_FOOTER2}</b>\n'
    )

    PNL_HEADER = f"<b>{emojies.PNL_HEADER1}برآیند امروز تیم رویال{emojies.PNL_HEADER2}</b>\n\n"

    PNL_ITEM = "<b><a href=\"{symbol_url}\">{symbol}</a> <a href=\"{status_url}\">{status}</a> — {pnl} {emoji}</b>\n"

    PNL_FOOTER = (
        f'{emojies.PNL_SPACER_FULL}\n\n'
        f'<b>{emojies.PNL_WINRATE} Win Rate: '+'{pnl}</b>\n\n'
        f'{emojies.PNL_SPACER_FULL}\n'
        f'{emojies.TELEGRAM} @Royal_frx'
    )

    PROFIT_SHOT = [
        (
            f'<b>شات سودشم بده دیگه{emojies.PROFIT_SHOT1}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT2} @Royal_admx</b>'
        ),
        (
            f'<b>حال میکنید نه ؟{emojies.PROFIT_SHOT3}</b>\n\n'
            f'<b>شما فقط شات سود بدید سیگنالش با ما{emojies.PROFIT_SHOT4}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT5} @Royal_admx</b>'
        ),
        (
            f'<b>برکت ، شات سود بدید سیگنال جدید بریم{emojies.PROFIT_SHOT6}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT7} @Royal_admx</b>'
        ),
        (
            f'<b>حال میکنید؟{emojies.PROFIT_SHOT8}</b>\n\n'
            f'<b>شات سود بدید گیفت بگیرید:</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT9} @Royal_admx</b>'
        ),
        (
            f'<b>سیو سود کنید{emojies.PROFIT_SHOT10}</b>\n\n'
            f'<b>شات سود بیاد پیوی{emojies.PROFIT_SHOT11}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT12} @Royal_admx</b>'
        ),
        (
            f'<b>نفری یه شات سود بفرستید رفقا :</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT13} @Royal_admx</b>'
        ),
        (
            f'<b>این همه خدمت میکنیم رایگان بهتون احساس وظیفه کنید و نفری یه شات یا یه نظر ارسال کنید به ما انرژی بدید{emojies.PROFIT_SHOT14}</b>\n\n'
            f'<b>با این کار بهای سیگنال هارو پبردازید حلالتون{emojies.PROFIT_SHOT15}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT16} @Royal_admx</b>'
        ),
        (
            f'<b>شات سود بفرستید برا عمو{emojies.PROFIT_SHOT7}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT18} @Royal_admx</b>'
        ),
        (
            f'<b>نوش جان همه ، شات سود بفرستید{emojies.PROFIT_SHOT19}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT20} @Royal_admx</b>'
        ),
        (
            f'<b>شات سود بدید پیوی عمو انرژی بگیره{emojies.PROFIT_SHOT21}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT22} @Royal_admx</b>'
        ),
        (
            f'<b>شات سود نداریم؟ ادامه ندم؟{emojies.PROFIT_SHOT23}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT24} @Royal_admx</b>'
        ),
        (
            f'<b>حالا شات سود بفرس و نظرتو درمورد سیگنال های عمو بگو{emojies.PROFIT_SHOT25}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT26} @Royal_admx</b>'
        ),
        (
            f'<b>شات سود هارو بفرستید پیوی{emojies.PROFIT_SHOT27}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT28} @Royal_admx</b>'
        ),
        (
            f'<b>من سیو سود کردم اینجا ، شما هم سیو کنید و شات سود بفرستید پیوی{emojies.PROFIT_SHOT29}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT30} @Royal_admx</b>'
        ),
        (
            f'<b>اگر سوالی داشتید پیوی بگید هستم پاسخ میدم ، شات سود هم ارسال بشه{emojies.PROFIT_SHOT31}</b>\n\n'
            f'<b>{emojies.PROFIT_SHOT32} @Royal_admx</b>'
        ),
    ]

    GOOD_NIGHT = (
        f'<b>شبتون بخیر عزیزان{emojies.GN_HEADER}</b>\n\n'
        f'<b>کانال آموزشی{emojies.GN_TEXT}:</b>\n\n'
        f'<b>{emojies.GN_CHANNEL} @royaltrade_ac</b>\n\n'
        f'<b>{emojies.ROYAL} @Royal_frx | رویال</b>'
    )

    GM_FOOTER = f'<b>{emojies.ROYAL} @Royal_frx | رویال</b>'

    GM_TEXTS = {
        1: (
            f'<b>صبحتون بخیر رویالی‌ها{emojies.GM1}\n\n'
            f'بازار همیشه هست؛ سرمایه و آرامشت را طوری حفظ کن که تو هم همیشه باشی.{emojies.GM2}</b>\n\n'
        ),
        2 : (
            f'<b>صبح بخیر دوستان عزیز{emojies.GM3}\n\n'
            f'ریسک از ندانستن کاری که انجام می‌دهی به وجود می‌آید. "وارن بافت"{emojies.GM4}</b>\n\n'
        ),
        3 : (
            f'<b>درود، صبح همگی بخیر{emojies.GM5}\n\n'
            f'صبور بودن، مزیتی است که بیشتر معامله‌گران حاضر نیستند بهایش را بپردازند.{emojies.GM6}</b>\n\n'
        ),
        4 : (
            f'<b>سلام، روزتون بخیر همراهان رویال{emojies.GM7}\n\n'
            f'بازار به کسی که منتظر بهترین فرصت می‌ماند، بیشتر از کسی که همیشه در معامله است پاداش می‌دهد{emojies.GM8}</b>\n\n'
        ),
        5 : (
            f'<b>صبح بخیر عزیزان{emojies.GM9}\n\n'
            f'حفظ سرمایه، اولین سود هر معامله‌گر حرفه‌ای است.{emojies.GM10}</b>\n\n'
        ),
        6 : (
            f'<b>{emojies.GM11}صبح بخیر قهرمان‌ها{emojies.GM12}\n\n'
            f'گاهی بهترین معامله، انجام ندادن هیچ معامله‌ای است.{emojies.GM13}</b>\n\n'
        ),
        7 : (
            f'<b>صبح بخیر خانواده رویال{emojies.GM14}\n\n'
            f'بازار می‌تواند بیشتر از آنچه تصور می‌کنی غیرمنطقی بماند. "جان مینارد کینز"{emojies.GM15}</b>\n\n'
        ),
        8 : (
            f'<b>صبح بخیر معامله‌گرهای عزیز{emojies.GM16}\n\n'
            f'سودهای بزرگ، نتیجه صدها تصمیم درست و کوچک هستند.{emojies.GM17}</b>\n\n'
        ),
        9 : (
            f'<b>سلام، صبح بخیر دوستان{emojies.GM18}\n\n'
            f'هیجان دشمن تصمیم‌های درست است.{emojies.GM19}</b>\n\n'
        ),
        10 : (
            f'<b>{emojies.GM20}روزتون بخیر همراهان{emojies.GM21}\n\n'
            f'هر ضرری یک هزینه است، اگر از آن درس بگیری.{emojies.GM22}</b>\n\n'
        ),
        11 : (
            f'<b> صبح بخیر همراهان رویال{emojies.GM23}\n\n'
            f'بازار به کسی بدهکار نیست؛ همیشه با احترام واردش شو {emojies.GM24}</b>\n\n'
        ),
        12 : (
            f'<b> سلام، صبح بخیر{emojies.GM25}\n\n'
            f'معامله‌گر موفق، قبل از سود به مدیریت ریسک فکر می‌کند.{emojies.GM26}</b>\n\n'
        ),
        13 : (
            f'<b>صبح بخیر دوستان رویالی{emojies.GM27}\n\n'
            f'«پول در صبر کردن ساخته می‌شود، نه در معامله کردن. "جسی لیورمور"{emojies.GM28}</b>\n\n'
        ),
        14 : (
            f'<b>درود، صبح بخیر عزیزان{emojies.GM29}\n\n'
            f'هر کندل فقط یک تصمیم را نشان می‌دهد، نه آینده را {emojies.GM30}</b>\n\n'
        ),
        15 : (
            f'<b>صبح بخیر رفقا{emojies.GM31}\n\n'
            f'اگر برنامه‌ای نداری، بازار برایت برنامه خواهد ساخت{emojies.GM32}</b>\n\n'
        ),
        16 : (
            f'<b>صبح بخیر همراهان ارزشمند{emojies.GM33}\n\n'
            f'انضباط، پلی است بین دانش و سود{emojies.GM34}</b>\n\n'
        ),
        17 : (
            f'<b>سلام بر دوستان، صبح بخیر{emojies.GM35}\n\n'
            f'فرصت‌های خوب، عجله نمی‌کنند{emojies.GM36}</b>\n\n'
        ),
        18 : (
            f'<b>صبح بخیر سرمایه‌گذارهای آینده{emojies.GM37}\n\n'
            f'هیچ روندی تا ابد ادامه ندارد{emojies.GM38}</b>\n\n'
        ),
        19 : (
            f'<b>صبح همگی بخیر{emojies.GM39}\n\n'
            f'امروز هم بازار همان بازار دیروز است؛ این تصمیم‌های ما هستند که نتیجه را تغییر می‌دهند{emojies.GM40}</b>\n\n'
        ),
        20 : (
            f'<b> صبح بخیر عزیزان{emojies.GM41}\n\n'
            f'اشتباهاتت را ثبت کن؛ سودها خودشان تکرار می‌شوند{emojies.GM42}</b>\n\n'
        ),
        21 : (
            f'<b>روزتون پر از آرامش{emojies.GM43}\n\n'
            f'معامله‌گر حرفه‌ای به دنبال قطعیت نیست؛ به دنبال احتمال بهتر است{emojies.GM44}</b>\n\n'
        ),
        22 : (
            f'<b>درود بر همراهان همیشگی{emojies.GM45}\n\n'
            f'بازار همیشه فرصت تازه‌ای برای افراد آماده دارد {emojies.GM46}</b>\n\n'
        ),
        23 : (
            f'<b>صبح بخیر دوستان{emojies.GM47}\n\n'
            f'طمع، سریع‌تر از هر ریزشی سرمایه را نابود می‌کند{emojies.GM48}</b>\n\n'
        ),
        24 : (
            f'<b>سلام، صبح بخیر همراهان{emojies.GM49}\n\n'
            f'بهترین تصمیم‌ها در آرام‌ترین ذهن‌ها گرفته می‌شوند{emojies.GM50}</b>\n\n'
        ),
        25 : (
            f'<b>صبح بخیر رویالی‌های عزیز{emojies.GM51}\n\n'
            f'هر روز بازار، یک کلاس درس رایگان است{emojies.GM52}</b>\n\n'
        ),
        26 : (
            f'<b>صبح بخیر قهرمانان بازار {emojies.GM53}\n\n'
            f'موفقیت در بازار، حاصل تکرار عادت‌های درست است{emojies.GM54}</b>\n\n'
        ),
        27 : (
            f'<b>صبح بخیر دوستان خوبم{emojies.GM55}\n\n'
            f'قبل از دنبال کردن سود، از سرمایه‌ات محافظت کن{emojies.GM56}</b>\n\n'
        ),
        28 : (
            f'<b>درود، روزتون بخیر{emojies.GM57}\n\n'
            f'هرگز همه تخم‌مرغ‌هایت را در یک سبد نگذار. "وارن بافت"{emojies.GM58}</b>\n\n'
        ),
        29 : (
            f'<b>صبح بخیر همراهان بازار {emojies.GM36}\n\n'
            f'صبر، ارزان‌ترین ابزار یک معامله‌گر و باارزش‌ترین دارایی اوست{emojies.GM46}</b>\n\n'
        ),
        30 : (
            f'<b> سلام، صبح بخیر دوستان عزیز{emojies.GM12}\n\n'
            f'موفقیت در بازار، قبل از نمودارها از ذهن شروع می‌شود{emojies.GM17}</b>\n\n'
        ),
        31: (
            f'<b>صبح بخیر دوستان عزیز{emojies.GM58}\n\n'
            f'عرضی نیست طولش ندیم{emojies.GM59}</b>\n\n'
        ),
    }
