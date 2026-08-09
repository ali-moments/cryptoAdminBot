from dataclasses import dataclass

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
from app.services.svg import SvgService
from app.market.cache import PriceCache
from app.market.dispatcher import EventDispatcher
from app.market.manager import ProviderManager
from app.market.providers.binance import BinanceProvider
from app.market.providers.bybit import BybitProvider
from app.market.providers.okx import OKXProvider
from app.market.events import PriceUpdatedEvent, ProviderChangedEvent
from app.database.enums import Provider
from app.engine.tracking_manager import TrackingManager
from app.engine.tracker import Tracker
from app.engine.action_processor import ActionProcessor
from app.market.subscription_manager import SubscriptionManager
from app.scheduler import AppScheduler


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

    scheduler: AppScheduler | None




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
    tg_sender = TelegramSender()
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
    dispatcher = EventDispatcher()
    price_cache = PriceCache()

    # Subscribe price cache to price update events
    dispatcher.subscribe(PriceUpdatedEvent, price_cache.on_price_updated)

    # Create market providers
    providers = {
        Provider.BINANCE: BinanceProvider(dispatcher),
        Provider.BYBIT: BybitProvider(dispatcher),
        Provider.OKX: OKXProvider(dispatcher),
    }

    market_manager = ProviderManager(
        dispatcher=dispatcher,
        cache=price_cache,
        providers=providers,
        primary=Provider.BINANCE,
        fallback=Provider.BYBIT,
        disaster=Provider.OKX,
    )

    # Engine components
    tracker = Tracker()
    
    # TelegramService already created above
    action_processor = ActionProcessor(telegram_service)

    tracking_manager = TrackingManager(
        uow_factory=UnitOfWork,
        tracker=tracker,
        processor=action_processor,
        cache=price_cache,
        interval=2.0,  # 2-second polling as per architecture
    )

    # Subscribe tracking manager to provider change events
    dispatcher.subscribe(ProviderChangedEvent, tracking_manager.on_provider_changed)

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
    )
    reader_manager = ReaderManager()
    reader_manager.subscribe(processor.handle)

    reader = TelegramReader(reader_manager)

    # Create application instance first
    app = Application(
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
        scheduler=None,  # Will be set below
    )

    # Create scheduler with telegram service and app reference
    scheduler = AppScheduler(telegram_service, app)
    
    # Set scheduler on app
    app.scheduler = scheduler

    return app
