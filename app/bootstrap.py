from dataclasses import dataclass
from loguru import logger

from app.database.uow import UnitOfWork
from app.telegram.parsers.bulls_signal import BullsSignalParser
from app.telegram.parsers.bulls_signal2 import BullsSignal2Parser
from app.telegram.parsers.bitcoin_bulls_vip import BitcoinBullsVIPParser
from app.telegram.parsers.gcr_vvip import GCRParser
from app.telegram.parsers.crypto_mermaids import CryptoMermaidsParser
from app.telegram.parsers.crypto_monk import CryptoMonkParser
from app.telegram.parsers.crypto_safe_calls import SafeCallParser
from app.telegram.parsers.sparta_crypto import SpartaCryptoParser
from app.telegram.parsers.crypto_aman import CryptoAmanParser
from app.telegram.parsers.mahee_vip import MaheeVIPParser
from app.telegram.parsers.crypto_traders_vip import CryptoTradersVIPParser
from app.market.symbol_registry import OurbitRegistry
from app.services.validation import ValidationService
from app.services.signal_lifecycle import SignalLifecycleService
from app.services.message_processor import MessageProcessor
from app.telegram.parsers.manager import ParserManager
from app.telegram.reader.client import TelegramReader
from app.telegram.reader.manager import ReaderManager
from app.services.telegram import TelegramService
from app.telegram.sender.client import TelegramSender
from app.telegram.sender.formatter import TelegramFormatter
from app.services.settings import States
from app.config.settings import settings
from app.services.svg import SvgService
from app.market.cache import PriceCache
from app.market.manager import ProviderManager
from app.market.registry import ProviderRegistry

from app.database.enums import Provider
from app.engine.tracking_manager import TrackingManager
from app.engine.tracker import Tracker
from app.engine.action_processor import ActionProcessor
from app.market.subscription_manager import SubscriptionManager
from app.scheduler import AppScheduler
from app.analytics.pnl import PnlAnalytics
from app.services.statistics import StatisticsService
from app.services.admin import AdminService
from app.telegram.admin.bot import AdminBot


@dataclass(slots=True)
class Application:
    registry: OurbitRegistry

    parser_manager: ParserManager

    validation: ValidationService

    lifecycle: SignalLifecycleService

    processor: MessageProcessor

    reader_manager: ReaderManager

    reader: TelegramReader

    sender: TelegramService

    market_manager: ProviderManager

    price_cache: PriceCache

    tracking_manager: TrackingManager

    subscription_manager: SubscriptionManager

    scheduler: AppScheduler

    admin_service: AdminService

    admin_bot: AdminBot




def build_application() -> Application:

    parser_manager = ParserManager()

    parser_manager.register("bulls_signal", BullsSignalParser())
    parser_manager.register("bulls_signal2", BullsSignal2Parser())
    parser_manager.register("bitcoin_bulls_vip", BitcoinBullsVIPParser())
    parser_manager.register("gcr_vvip", GCRParser())
    parser_manager.register("crypto_mermaids", CryptoMermaidsParser())
    parser_manager.register("crypto_monk", CryptoMonkParser())
    parser_manager.register("crypto_safe_calls", SafeCallParser())
    parser_manager.register("sparta_crypto", SpartaCryptoParser())
    parser_manager.register("crypto_aman", CryptoAmanParser())
    parser_manager.register("mahee_vip", MaheeVIPParser())
    parser_manager.register("crypto_traders_vip", CryptoTradersVIPParser())

    registry = OurbitRegistry()
    validation = ValidationService(registry)
    states = States()

    # Create TelegramService components first
    svg_service = SvgService()
    tg_sender = TelegramSender(uow_factory=UnitOfWork)
    tg_formatter = TelegramFormatter()
    telegram_service = TelegramService(
        sender=tg_sender,
        formatter=tg_formatter,
        svg=svg_service,
        states=states,
        uow_factory=UnitOfWork,
    )

    # Now create lifecycle with telegram_service dependency
    lifecycle = SignalLifecycleService(states=states, telegram_service=telegram_service)

    # Market components
    price_cache = PriceCache()

    # Create market providers with polling intervals
    providers = ProviderRegistry.create_all_providers(
        cache=price_cache,
        polling_intervals={
            Provider.BINANCE: settings.binance_polling_interval,
            Provider.BYBIT: settings.bybit_polling_interval,
            Provider.OKX: settings.okx_polling_interval,
        }
    )

    market_manager = ProviderManager(
        cache=price_cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
        consecutive_miss_threshold=settings.consecutive_miss_threshold,
    )
    logger.info(f"MANAGER_CREATED: ProviderManager instance created, manager_id={id(market_manager)}")
    logger.info(f"PROVIDER_IDS: BINANCE={id(providers[Provider.BINANCE])}, BYBIT={id(providers[Provider.BYBIT])}, OKX={id(providers[Provider.OKX])}")

    # Engine components
    tracker = Tracker()

    # TelegramService already created above
    action_processor = ActionProcessor(telegram_service)

    tracking_manager = TrackingManager(
        uow_factory=UnitOfWork,
        tracker=tracker,
        processor=action_processor,
        cache=price_cache,
        interval=7.0,  # 5-second polling as per REST API design
    )



    subscription_manager = SubscriptionManager(
        uow_factory=UnitOfWork,
        provider_manager=market_manager,
        interval=5.0,  # 5-second polling for subscription sync
    )

    processor = MessageProcessor(
        uow_factory=UnitOfWork,
        parser_manager=parser_manager,
        validation_service=validation,
        signal_lifecycle=lifecycle,
        states=states,
    )
    reader_manager = ReaderManager()
    reader_manager.subscribe(processor.handle)

    reader = TelegramReader(reader_manager)

    # Create analytics components
    pnl_analytics = PnlAnalytics(UnitOfWork, price_cache)

    # Create scheduler with telegram service and analytics
    scheduler = AppScheduler(telegram_service, pnl_analytics)

    # Create statistics service for admin bot
    statistics_service = StatisticsService(UnitOfWork)

    # Create admin service with telegram integration
    admin_service = AdminService(
        uow_factory=UnitOfWork,
        statistics_service=statistics_service,
        states=states,
        telegram_service=telegram_service,  # Add telegram service dependency
    )

    # Create admin bot
    admin_bot = AdminBot(admin_service)

    return Application(
        registry=registry,
        parser_manager=parser_manager,
        validation=validation,
        lifecycle=lifecycle,
        processor=processor,
        reader_manager=reader_manager,
        reader=reader,
        sender=telegram_service,
        market_manager=market_manager,
        price_cache=price_cache,
        tracking_manager=tracking_manager,
        subscription_manager=subscription_manager,
        scheduler=scheduler,
        admin_service=admin_service,
        admin_bot=admin_bot,
    )
