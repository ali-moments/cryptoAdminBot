from dataclasses import dataclass

from app.market.dto import PriceTick


@dataclass(slots=True)
class PriceUpdatedEvent:
    tick: PriceTick