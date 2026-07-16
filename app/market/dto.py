from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.database.enums import Provider


@dataclass(slots=True)
class PriceTick:
    symbol: str

    price: Decimal

    provider: Provider

    timestamp: datetime


@dataclass(slots=True)
class ProviderState:
    provider: Provider

    connected: bool

    last_seen: datetime | None


@dataclass(slots=True)
class Subscription:
    symbol: str

    subscribers: int = 0
