from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any
from loguru import logger

EventHandler = Callable[[Any], Awaitable[None]]


class EventDispatcher:
    def __init__(self) -> None:
        self._handlers: dict[type, list[EventHandler]] = defaultdict(list)

    def subscribe(
        self,
        event_type: type,
        handler: EventHandler,
    ) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    def unsubscribe(
        self,
        event_type: type,
        handler: EventHandler,
    ) -> None:
        self._handlers[event_type].remove(handler)

        if not self._handlers[event_type]:
            del self._handlers[event_type]

    async def publish(
        self,
        event: Any,
    ) -> None:
        logger.debug(f"EVENT_DISPATCH: {type(event).__name__} to {len(self._handlers[type(event)])} handlers")
        for handler in self._handlers[type(event)]:
            await handler(event)
