from dataclasses import dataclass

from app.database.enums import Provider
from app.market.dto import PriceTick


@dataclass(slots=True)
class PriceUpdatedEvent:
    tick: PriceTick


@dataclass(slots=True)
class ProviderConnectedEvent:
    provider: Provider


@dataclass(slots=True)
class ProviderDisconnectedEvent:
    provider: Provider


@dataclass(slots=True)
class ProviderChangedEvent:
    previous: Provider

    current: Provider
