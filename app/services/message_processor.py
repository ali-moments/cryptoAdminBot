from collections.abc import Callable
from loguru import logger
from app.database.uow import UnitOfWork
from app.services.signal_lifecycle import SignalLifecycleService
from app.services.validation import ValidationService
from app.telegram.parsers.manager import ParserManager
from app.telegram.reader.events import TelegramMessageReceived


class MessageProcessor:
    def __init__(
        self,
        uow_factory: Callable[[], UnitOfWork],
        parser_manager: ParserManager,
        validation_service: ValidationService,
        signal_lifecycle: SignalLifecycleService,
    ) -> None:
        self._uow_factory = uow_factory
        self._parser_manager = parser_manager
        self._validation_service = validation_service
        self._signal_lifecycle = signal_lifecycle

    async def handle(
        self,
        event: TelegramMessageReceived,
    ) -> None:
        try:
            channel_id = event.message.channel_id
            if event.message.is_forwarded:
                channel_id = event.message.forwarded_chat_id
            async with self._uow_factory() as uow:
                source = await uow.signal_sources.get_by_channel_id(channel_id)
                logger.trace("channel_id: {}", channel_id)
                if source is None:
                    logger.trace("source is None.")
                    return

                if not source.is_active:
                    logger.trace("source is not active.")
                    return

                parser = self._parser_manager.get(
                    source.parser_name,
                )

                if parser is None:
                    logger.trace("parser not found")
                    return

                parsed_signal = await parser.parse(
                    event.message,
                )

                if parsed_signal is None:
                    logger.trace("signal not parsed.")
                    return

                logger.trace("parsed_signal:\n{}", parsed_signal)
                validated_signal = await self._validation_service.validate(
                    parsed_signal,
                    uow,
                )

                if validated_signal is None:
                    logger.trace("signal is not valid.")
                    return

                logger.trace("validated signal:\n {}", validated_signal)
                await self._signal_lifecycle.create_signal(
                    validated_signal,
                    source,
                    uow,
                )

                await uow.commit()

        except Exception as e:
            # TODO:
            logger.exception("{}", e)
            # create AuditLog(ERROR)
            raise
