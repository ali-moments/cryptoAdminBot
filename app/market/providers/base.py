from abc import ABC, abstractmethod

from app.database.enums import Provider
from app.market.dispatcher import EventDispatcher
from app.market.dto import PriceTick
from app.market.events import PriceUpdatedEvent


class BaseProvider(ABC):
    def __init__(
        self,
        dispatcher: EventDispatcher,
    ) -> None:
        self._dispatcher = dispatcher
        self._connected = False

    @property
    @abstractmethod
    def name(self) -> Provider:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        return self._connected

    @abstractmethod
    async def connect(
        self,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def disconnect(
        self,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def subscribe(
        self,
        symbol: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def unsubscribe(
        self,
        symbol: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def current_price(
        self,
        symbol: str,
    ) -> PriceTick | None:
        raise NotImplementedError

    async def _publish_price(
        self,
        tick: PriceTick,
    ) -> None:
        await self._dispatcher.publish(
            PriceUpdatedEvent(
                tick=tick,
            )
        )
