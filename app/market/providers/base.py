from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

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
        self._last_global_ticker: Optional[datetime] = None
        self._connection_time: Optional[datetime] = None

    @property
    @abstractmethod
    def name(self) -> Provider:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property 
    def is_healthy(self) -> bool:
        """Global provider health - socket connected and recent ticker activity"""
        if not self._connected or not self._last_global_ticker:
            return False
        
        age = (datetime.now(timezone.utc) - self._last_global_ticker).total_seconds()
        return age < 45  # Recent data within 45 seconds
    
    @property
    def connection_time(self) -> Optional[datetime]:
        """When this provider last connected"""
        return self._connection_time
        
    def mark_connected(self) -> None:
        """Mark provider as connected and record connection time"""
        self._connected = True
        self._connection_time = datetime.now(timezone.utc)
        
    def mark_disconnected(self) -> None:
        """Mark provider as disconnected and clear health state"""
        self._connected = False
        self._connection_time = None
        self._last_global_ticker = None

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
        # Update global health on any price received
        self._last_global_ticker = datetime.now(timezone.utc)
        
        await self._dispatcher.publish(
            PriceUpdatedEvent(
                tick=tick,
            )
        )
