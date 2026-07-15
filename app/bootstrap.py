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
from app.services.ourbit_registry import OurbitRegistry
from app.services.validation import ValidationService
from app.services.signal_lifecycle import SignalLifecycleService
from app.services.message_processor import MessageProcessor
from app.telegram.parsers.manager import ParserManager
from app.telegram.reader.client import TelegramReader
from app.telegram.reader.manager import ReaderManager


@dataclass(slots=True)
class Application:
    registry: OurbitRegistry

    parser_manager: ParserManager

    validation: ValidationService

    lifecycle: SignalLifecycleService

    processor: MessageProcessor

    reader_manager: ReaderManager

    reader: TelegramReader




def build_application() -> Application:
    registry = OurbitRegistry()

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


    validation = ValidationService(
        registry,
    )

    lifecycle = SignalLifecycleService()

    processor = MessageProcessor(
        uow_factory=UnitOfWork,
        parser_manager=parser_manager,
        validation_service=validation,
        signal_lifecycle=lifecycle,
    )

    reader_manager = ReaderManager()

    reader_manager.subscribe(
        processor.handle,
    )

    reader = TelegramReader(
        reader_manager,
    )

    return Application(
        registry=registry,
        parser_manager=parser_manager,
        validation=validation,
        lifecycle=lifecycle,
        processor=processor,
        reader_manager=reader_manager,
        reader=reader,
    )
