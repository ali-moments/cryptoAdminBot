from dataclasses import dataclass

from app.database.uow import UnitOfWork
from app.telegram.parsers.bulls import BullsSignalParser
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

    parser_manager.register(
        "bulls",
        BullsSignalParser(),
    )

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
