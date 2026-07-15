from collections.abc import Awaitable, Callable

from app.telegram.reader.events import TelegramMessageReceived

MessageHandler = Callable[
    [TelegramMessageReceived],
    Awaitable[None],
]


class ReaderManager:
    def __init__(self) -> None:
        self._handlers: list[MessageHandler] = []

    def subscribe(
        self,
        handler: MessageHandler,
    ) -> None:
        self._handlers.append(handler)

    async def dispatch(
        self,
        event: TelegramMessageReceived,
    ) -> None:
        for handler in self._handlers:
            await handler(event)
